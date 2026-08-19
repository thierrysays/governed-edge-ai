"""
MockR4Supervisor: software model of the Arduino UNO R4 WiFi oversight node.

Same pattern as MockSTM32H5: a Unix pty pair whose slave end is exposed as
.device, so the VENTUNO Q connects to it exactly as it would to the real
board over USB-C. The mock runs the R4's state machine, and the C++ sketch in
r4-supervisor/ mirrors it gate for gate. This module is the executable
specification; the sketch is the port.

State machine
-------------
    WATCHING  --button press-------------------->  OVERRIDE
    WATCHING  --heartbeat older than timeout---->  OVERRIDE
    WATCHING  --digest gap or rollback---------->  OVERRIDE
    OVERRIDE  --clear_override(), only when the button is released
                and a fresh heartbeat has arrived------------>  WATCHING

The override latches. It does not lapse when the condition that raised it
goes away, and the governance tier has no message that clears it: releasing
an override is a physical act at the oversight node. That asymmetry is the
control. Anything that can silence its own supervisor is not supervised.

Two enforcement paths
---------------------
1. Soft veto: OVERRIDE_ASSERT on the serial link. The GovernanceFilter stops
   transmitting CommandRequest frames.
2. Physical: a bistable latch relay whose contact sits in series with the
   Alvik's motor supply, driven over this node's own I2C bus. It holds even
   if the VENTUNO Q ignores the soft veto, because there is no motor supply
   to ignore it with, and it holds through a power cut and a Linux reboot
   because the contact is bistable. See oversight/latch.py.

   This replaces an earlier GPIO line into the Alvik's kill-switch pin, which
   failed open on power loss and worked only because the Alvik's firmware
   chose to read that pin. Both faults are gone: the relay needs no
   cooperation from the governed board and no current to hold its position.

Attestation limits
------------------
The R4 stores digests, not rows, so it cannot recompute the hash chain. Its
live verdict covers what the digest stream alone can prove: strict audit_ref
monotonicity with no gaps (see AttestVerdict). Detecting an altered row is
the offline job of oversight.attestation.verify_database(), run against the
digests read back from this node.
"""

from __future__ import annotations

import contextlib
import os
import select
import threading
import time
import tty
from collections import deque
from dataclasses import dataclass

from ipc.codec import (
    AttestAck,
    AttestDigest,
    AttestVerdict,
    FrameParser,
    LatchPosition,
    LatchReport,
    LatchRequest,
    OverrideAssert,
    OverrideClear,
    OverrideReason,
    SupervisorHeartbeat,
    encode,
)
from oversight.latch import LatchRelay, LatchState, SimulatedLatch

HEARTBEAT_TIMEOUT_MS_DEFAULT: float = 2000.0
DIGEST_CAPACITY_DEFAULT: int = 64

# Glyphs on the 12x8 LED matrix. The matrix is the only status surface on the
# board, and it is driven from the state machine, never from the link.
ANNUNCIATOR_WATCHING: str = "WATCHING"       # steady outline
ANNUNCIATOR_OVERRIDE: str = "OVERRIDE"       # solid block, latched
ANNUNCIATOR_STALE: str = "STALE"             # governance heartbeat lost
ANNUNCIATOR_ATTEST_ALERT: str = "ATTEST"     # chain gap or rollback seen
ANNUNCIATOR_LATCH_FAULT: str = "LATCH"       # the relay is not where it was told


@dataclass
class SupervisorStats:
    heartbeats_received: int = 0
    digests_received: int = 0
    overrides_asserted: int = 0
    overrides_cleared: int = 0
    chain_faults: int = 0
    latch_requests: int = 0
    latch_requests_refused: int = 0
    latch_mismatches: int = 0


class MockR4Supervisor:
    """
    Parameters
    ----------
    heartbeat_timeout_ms:
        Silence from the VENTUNO Q beyond this raises an override with reason
        GOVERNANCE_HEARTBEAT_LOST. Must be several heartbeat intervals so a
        single dropped frame does not halt the rig.
    digest_capacity:
        How many (audit_ref, digest) pairs the node retains. Models the R4's
        limited EEPROM: a ring buffer, oldest evicted first.
    latch:
        The latch relay this node owns. None builds one over a SimulatedLatch,
        which is what a complete simulated node has.
    """

    def __init__(
        self,
        heartbeat_timeout_ms: float = HEARTBEAT_TIMEOUT_MS_DEFAULT,
        digest_capacity: int = DIGEST_CAPACITY_DEFAULT,
        latch: LatchRelay | None = None,
    ) -> None:
        self._timeout_s = heartbeat_timeout_ms / 1000.0
        self._capacity = digest_capacity
        self._latch = latch if latch is not None else LatchRelay(SimulatedLatch())

        self._master_fd, self._slave_fd = os.openpty()
        tty.setraw(self._slave_fd)
        self._slave_path: str = os.ttyname(self._slave_fd)

        self._lock = threading.Lock()
        self._override = False
        self._override_reason: OverrideReason | None = None
        self._button_pressed = False
        self._chain_alert = False

        self._last_ref = 0
        self._digests: deque[tuple[int, bytes]] = deque(maxlen=digest_capacity)
        self._last_heartbeat: SupervisorHeartbeat | None = None
        # Freshness is measured from construction, so a node that has never
        # heard anything goes stale on schedule rather than never.
        self._last_heartbeat_at: float = time.monotonic()

        self._stats = SupervisorStats()
        self._parser = FrameParser()
        self._running = False
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Timer | None = None
        self._t0 = time.monotonic()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def device(self) -> str:
        """Slave pty path: the VENTUNO Q opens this as its serial port."""
        return self._slave_path

    @property
    def override_active(self) -> bool:
        with self._lock:
            return self._override

    @property
    def override_reason(self) -> OverrideReason | None:
        with self._lock:
            return self._override_reason

    @property
    def latch(self) -> LatchRelay:
        """The relay this node owns. Nothing else in the system can reach it."""
        return self._latch

    @property
    def motor_power_cut(self) -> bool:
        """True when the contact is **observed** open: the motors are isolated.

        Read from the sense line, not from what was commanded. False before
        the first poll and false while the sense line is unreadable, because
        neither is evidence that the motors are isolated. That asymmetry is
        deliberate: this property is only ever allowed to claim safety it has
        seen.
        """
        return self._latch.enforcing

    @property
    def halt_intended(self) -> bool:
        """True when the node wants the motors isolated, whatever the relay did.

        Differs from `motor_power_cut` exactly when something is wrong, which
        is the pair worth watching: intent without effect is a failed relay.
        """
        with self._lock:
            return self._override or self._last_heartbeat is None

    @property
    def annunciator(self) -> str:
        """Glyph currently shown on the 12x8 LED matrix."""
        with self._lock:
            if self._override:
                if self._override_reason == OverrideReason.GOVERNANCE_HEARTBEAT_LOST:
                    return ANNUNCIATOR_STALE
                if self._override_reason == OverrideReason.ATTESTATION_MISMATCH:
                    return ANNUNCIATOR_ATTEST_ALERT
                if self._override_reason == OverrideReason.LATCH_MISMATCH:
                    return ANNUNCIATOR_LATCH_FAULT
                return ANNUNCIATOR_OVERRIDE
            return ANNUNCIATOR_WATCHING

    @property
    def retained_digests(self) -> list[tuple[int, bytes]]:
        """Digests held independently of the governance host, oldest first.

        Feed these to oversight.attestation.verify_database() to reconcile the
        SQLite log against what the oversight node actually witnessed.
        """
        with self._lock:
            return list(self._digests)

    @property
    def last_heartbeat(self) -> SupervisorHeartbeat | None:
        with self._lock:
            return self._last_heartbeat

    @property
    def stats(self) -> SupervisorStats:
        with self._lock:
            return SupervisorStats(**vars(self._stats))

    def press_button(self) -> None:
        """Operator presses the physical override button on the R4."""
        with self._lock:
            self._button_pressed = True
        self._assert_override(OverrideReason.OPERATOR_BUTTON)

    def release_button(self) -> None:
        """Operator releases the button. The override stays latched."""
        with self._lock:
            self._button_pressed = False

    def remote_override(self) -> None:
        """Override raised from the R4's own Wi-Fi console, not the button."""
        self._assert_override(OverrideReason.REMOTE_CONSOLE)

    def clear_override(self) -> bool:
        """
        Release a latched override. Returns False when refused.

        Refused while the button is still held, and until a heartbeat has
        actually arrived and is still fresh: clearing an override whose cause
        is still present would put the rig straight back into the state that
        raised it, and a node that has never heard from the governance tier
        cannot conclude that the tier is healthy.

        Clearing an attestation override also resynchronises the expected
        reference to whatever the governance tier last reported, otherwise
        every subsequent digest would gap against a stale expectation and the
        node could never resume. The gap itself does not disappear: it stays
        in the retained digests, and clearing is the record that an operator
        looked at it and accepted it.
        """
        frame: bytes | None = None
        with self._lock:
            if not self._override:
                return True
            if self._button_pressed:
                return False
            if self._last_heartbeat is None or self._heartbeat_stale_locked():
                return False
            if self._chain_alert and self._last_heartbeat is not None:
                self._last_ref = max(
                    self._last_ref, self._last_heartbeat.last_audit_ref
                )
            self._override = False
            self._override_reason = None
            self._chain_alert = False
            self._stats.overrides_cleared += 1
            frame = encode(OverrideClear(timestamp_us=self._timestamp_us()))
        self._latch.permit()
        self._write(frame)
        self._write(self._latch_report_frame())
        return True

    def start(self) -> MockR4Supervisor:
        # Isolate the motors before doing anything else. The contact is
        # bistable, so it comes up wherever it was left, and the node must
        # not assume that is where it wants it.
        self._latch.enforce_halt()
        self._running = True
        self._last_heartbeat_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._read_loop, daemon=True, name="mock-r4-supervisor"
        )
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

    def __enter__(self) -> MockR4Supervisor:
        return self.start()

    def __exit__(self, *_: object) -> None:
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
            self._poll_latch()
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
        if isinstance(msg, SupervisorHeartbeat):
            self._on_heartbeat(msg)
        elif isinstance(msg, AttestDigest):
            self._on_digest(msg)
        elif isinstance(msg, LatchRequest):
            self._on_latch_request(msg)

    # ------------------------------------------------------------------
    # Latch
    # ------------------------------------------------------------------

    def _poll_latch(self) -> None:
        """Read the contact back at the configured cadence.

        Polling rather than waiting for a transition is what catches a relay
        that silently failed to move: an edge that never happened raises no
        interrupt. A disagreement between what was commanded, what the module
        reports and what the sense line observes latches an override, because
        a safety contact that is not where it was told to be is a fault
        whatever direction it is in.
        """
        reading = self._latch.poll_if_due()
        if reading is None:
            return
        if reading.commanded is LatchState.UNKNOWN:
            return
        if reading.agrees:
            return
        with self._lock:
            self._stats.latch_mismatches += 1
        self._write(self._latch_report_frame())
        self._assert_override(OverrideReason.LATCH_MISMATCH)

    def _latch_report_frame(self) -> bytes:
        reading = self._latch.last_reading
        if reading is None:
            reading = self._latch.poll()
        return encode(LatchReport(
            commanded=LatchPosition(int(reading.commanded)),
            reported=LatchPosition(int(reading.reported)),
            observed=LatchPosition(int(reading.observed)),
            transitions=self._latch.transitions,
            mismatches=self._latch.mismatches,
        ))

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _on_heartbeat(self, hb: SupervisorHeartbeat) -> None:
        first: bool
        with self._lock:
            first = self._last_heartbeat is None
            self._stats.heartbeats_received += 1
            self._last_heartbeat = hb
            self._last_heartbeat_at = time.monotonic()
            override = self._override
        self._arm_watchdog()
        # The node boots with the contact open and releases it on first
        # contact with a live governance tier. No latch, no arming step: a
        # tier that has said nothing has not earned the authority to move a
        # robot. An override already latched keeps the contact open.
        if first and not override:
            self._latch.permit()
            self._write(self._latch_report_frame())

    def _on_latch_request(self, req: LatchRequest) -> None:
        """A request from the governance tier. The arbiter decides.

        A request to close while an override is latched is refused. That
        refusal is the reason the relay is owned by this board and not by the
        one asking.
        """
        with self._lock:
            self._stats.latch_requests += 1
            refused = self._override and req.desired is LatchPosition.CLOSED
            if refused:
                self._stats.latch_requests_refused += 1
        if not refused:
            if req.desired is LatchPosition.OPEN:
                self._latch.enforce_halt()
            else:
                self._latch.permit()
        self._write(self._latch_report_frame())

    def _on_digest(self, msg: AttestDigest) -> None:
        verdict: AttestVerdict
        with self._lock:
            self._stats.digests_received += 1
            if msg.audit_ref <= self._last_ref:
                verdict = AttestVerdict.CHAIN_BREAK
            elif msg.audit_ref > self._last_ref + 1:
                verdict = AttestVerdict.GAP
            else:
                verdict = AttestVerdict.CHAIN_OK

            if verdict is AttestVerdict.CHAIN_OK:
                self._digests.append((msg.audit_ref, msg.digest))
                self._last_ref = msg.audit_ref
            else:
                self._chain_alert = True
                self._stats.chain_faults += 1
            ack = encode(AttestAck(audit_ref=msg.audit_ref, verdict=verdict))

        self._write(ack)
        if verdict is not AttestVerdict.CHAIN_OK:
            self._assert_override(OverrideReason.ATTESTATION_MISMATCH)

    # ------------------------------------------------------------------
    # Override latch and watchdog
    # ------------------------------------------------------------------

    def _assert_override(self, reason: OverrideReason) -> None:
        """Latch an override and tell the governance tier. Idempotent.

        The first reason wins: once latched, a later trigger does not relabel
        the record of what actually stopped the rig.
        """
        frame: bytes | None = None
        with self._lock:
            if not self._override:
                self._override = True
                self._override_reason = reason
                self._stats.overrides_asserted += 1
                frame = encode(OverrideAssert(
                    timestamp_us=self._timestamp_us(), reason=reason,
                ))
        if frame:
            # Open the contact before announcing it. If the announcement is
            # the thing that fails, the motors are already isolated.
            self._latch.enforce_halt()
            self._write(frame)
            self._write(self._latch_report_frame())

    def _heartbeat_stale_locked(self) -> bool:
        return (time.monotonic() - self._last_heartbeat_at) > self._timeout_s

    def _arm_watchdog(self) -> None:
        if self._watchdog:
            self._watchdog.cancel()
        self._watchdog = threading.Timer(self._timeout_s, self._on_watchdog_expired)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _on_watchdog_expired(self) -> None:
        if not self._running:
            return
        self._assert_override(OverrideReason.GOVERNANCE_HEARTBEAT_LOST)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write(self, frame: bytes | None) -> None:
        if not frame:
            return
        with contextlib.suppress(OSError):
            os.write(self._master_fd, frame)

    def _timestamp_us(self) -> int:
        return int((time.monotonic() - self._t0) * 1_000_000)
