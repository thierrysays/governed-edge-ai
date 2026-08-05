"""
MockSTM32H5: software simulation of the STM32H5 real-time co-processor.

Opens a Unix pseudo-terminal pair. The slave end is exposed as .device
(a /dev/pts/N path) so any serial-port client can connect to it exactly
as it would connect to a real UART. The mock reads from the master end,
applies the protocol state machine from docs/ipc-protocol.md, and writes
responses back through the master end.

Usage (as context manager):
    with MockSTM32H5() as peer:
        client = open(peer.device, "rb+", buffering=0)
        client.write(encode(Heartbeat()))
        ...

State machine:
    ARMED  → BUSY (on accepted COMMAND_REQUEST)
    BUSY   → ARMED (on command completion, simulated instantly)
    ARMED  → HALTED (kill switch GPIO or watchdog expiry)
    HALTED → ARMED (not automatic; requires kill switch release +
                    STATUS_QUERY — resume sequence TBD per spec)
    any    → FAULT (explicit inject_fault() call)

Governance invariants enforced:
    - audit_ref == 0 → COMMAND_REJECT(AUDIT_REF_ZERO) always
    - confidence < threshold → COMMAND_REJECT(CONFIDENCE_BELOW_THRESHOLD)
    - kill switch open → COMMAND_REJECT(KILL_SWITCH_ACTIVE)
    - watchdog expired → transition to HALTED + HALT_NOTIFY within spec deadline
    - STM32H5 is sole execution authority: no path bypasses _handle_command
"""

import contextlib
import os
import select
import threading
import time
import tty
from dataclasses import dataclass

from ipc.codec import (
    AckStatus,
    CommandAck,
    CommandReject,
    CommandRequest,
    FrameParser,
    HaltNotify,
    HaltTrigger,
    Heartbeat,
    HeartbeatAck,
    RejectReason,
    StatusQuery,
    StatusResponse,
    SystemState,
    encode,
)

CONFIDENCE_THRESHOLD_DEFAULT: float = 0.70
WATCHDOG_MS_DEFAULT: float = 1000.0


@dataclass
class PeerStats:
    commands_received: int = 0
    commands_rejected: int = 0
    commands_executed: int = 0


class MockSTM32H5:
    def __init__(
        self,
        confidence_threshold: float = CONFIDENCE_THRESHOLD_DEFAULT,
        watchdog_ms: float = WATCHDOG_MS_DEFAULT,
    ) -> None:
        self._threshold = confidence_threshold
        self._watchdog_ms = watchdog_ms

        # pty pair — binary, raw (no line-discipline mangling)
        self._master_fd, self._slave_fd = os.openpty()
        tty.setraw(self._slave_fd)  # prevent 0x0A → 0x0D 0x0A translation
        self._slave_path: str = os.ttyname(self._slave_fd)

        # State
        self._state = SystemState.ARMED
        self._kill_switch_open = False
        self._lock = threading.Lock()

        # Stats
        self._stats = PeerStats()

        # IPC reader
        self._parser = FrameParser()
        self._running = False
        self._thread: threading.Thread | None = None

        # Watchdog
        self._t0 = time.monotonic()
        self._watchdog: threading.Timer | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def device(self) -> str:
        """Slave pty path — open this as a serial port from the client."""
        return self._slave_path

    @property
    def state(self) -> SystemState:
        with self._lock:
            return self._state

    @property
    def stats(self) -> PeerStats:
        with self._lock:
            import copy
            return copy.copy(self._stats)

    def trigger_kill_switch(self) -> None:
        """Simulate physical NC kill-switch being opened."""
        frame: bytes | None = None
        with self._lock:
            self._kill_switch_open = True
            if self._state not in (SystemState.HALTED, SystemState.FAULT):
                self._state = SystemState.HALTED
                frame = encode(HaltNotify(
                    timestamp_us=self._timestamp_us(),
                    trigger=HaltTrigger.KILL_SWITCH_GPIO,
                ))
        if frame:
            self._write(frame)

    def release_kill_switch(self) -> None:
        """Simulate physical kill-switch returning to closed (NC)."""
        with self._lock:
            self._kill_switch_open = False

    def inject_fault(self) -> None:
        """Force the peer into FAULT state (unrecoverable until restart)."""
        with self._lock:
            self._state = SystemState.FAULT

    def start(self) -> "MockSTM32H5":
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name="mock-stm32h5")
        self._thread.start()
        self._arm_watchdog()
        return self

    def stop(self) -> None:
        self._running = False
        if self._watchdog:
            self._watchdog.cancel()
        if self._thread:
            self._thread.join(timeout=0.5)
        for fd in (self._master_fd, self._slave_fd):
            with contextlib.suppress(OSError):
                os.close(fd)

    def __enter__(self) -> "MockSTM32H5":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        while self._running:
            try:
                ready, _, _ = select.select([self._master_fd], [], [], 0.05)
            except (ValueError, OSError):
                break
            if not ready:
                continue
            try:
                data = os.read(self._master_fd, 512)
            except OSError:
                break
            self._parser.feed(data)
            for msg in self._parser.pop_messages():
                self._dispatch(msg)

    def _dispatch(self, msg: object) -> None:
        if isinstance(msg, Heartbeat):
            self._on_heartbeat()
        elif isinstance(msg, StatusQuery):
            self._on_status_query()
        elif isinstance(msg, CommandRequest):
            self._on_command_request(msg)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _on_heartbeat(self) -> None:
        self._arm_watchdog()
        self._write(encode(HeartbeatAck()))

    def _on_status_query(self) -> None:
        with self._lock:
            resp = StatusResponse(
                system_state=self._state,
                kill_switch_gpio=int(self._kill_switch_open),
                commands_received=self._stats.commands_received,
                commands_rejected=self._stats.commands_rejected,
                commands_executed=self._stats.commands_executed,
            )
        self._write(encode(resp))

    def _on_command_request(self, req: CommandRequest) -> None:
        response_frame: bytes
        with self._lock:
            self._stats.commands_received += 1

            # Reject gate 1: zero audit_ref (log-before-act violated)
            if req.audit_ref == 0:
                self._stats.commands_rejected += 1
                response_frame = encode(CommandReject(
                    audit_ref=0, reason=RejectReason.AUDIT_REF_ZERO,
                ))

            # Reject gate 2: kill switch
            elif self._kill_switch_open:
                self._stats.commands_rejected += 1
                response_frame = encode(CommandReject(
                    audit_ref=req.audit_ref, reason=RejectReason.KILL_SWITCH_ACTIVE,
                ))

            # Reject gate 3: system in HALTED state (watchdog or prior kill)
            elif self._state == SystemState.HALTED:
                self._stats.commands_rejected += 1
                response_frame = encode(CommandReject(
                    audit_ref=req.audit_ref, reason=RejectReason.WATCHDOG_TIMEOUT,
                ))

            # Reject gate 4: FAULT state
            elif self._state == SystemState.FAULT:
                self._stats.commands_rejected += 1
                response_frame = encode(CommandReject(
                    audit_ref=req.audit_ref, reason=RejectReason.SYSTEM_FAULT,
                ))

            # Reject gate 5: confidence below STM32H5 threshold (dual gate)
            elif req.confidence < self._threshold:
                self._stats.commands_rejected += 1
                response_frame = encode(CommandReject(
                    audit_ref=req.audit_ref,
                    reason=RejectReason.CONFIDENCE_BELOW_THRESHOLD,
                ))

            # Accept: ACK and execute
            else:
                self._stats.commands_executed += 1
                self._state = SystemState.BUSY
                response_frame = encode(CommandAck(
                    audit_ref=req.audit_ref, status=AckStatus.EXECUTING,
                ))
                # Simulated instant execution: return to ARMED
                self._state = SystemState.ARMED

        self._write(response_frame)

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def _arm_watchdog(self) -> None:
        if self._watchdog:
            self._watchdog.cancel()
        self._watchdog = threading.Timer(
            self._watchdog_ms / 1000.0, self._on_watchdog_expired
        )
        self._watchdog.daemon = True
        self._watchdog.start()

    def _on_watchdog_expired(self) -> None:
        frame: bytes | None = None
        with self._lock:
            if self._state not in (SystemState.HALTED, SystemState.FAULT):
                self._state = SystemState.HALTED
                frame = encode(HaltNotify(
                    timestamp_us=self._timestamp_us(),
                    trigger=HaltTrigger.WATCHDOG,
                ))
        if frame:
            self._write(frame)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write(self, frame: bytes) -> None:
        with contextlib.suppress(OSError):
            os.write(self._master_fd, frame)

    def _timestamp_us(self) -> int:
        return int((time.monotonic() - self._t0) * 1_000_000)
