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
2. Hard line: a GPIO output driven into the Alvik kill-switch input, modelled
   here by `kill_line_asserted`. It holds even if the VENTUNO Q ignores the
   soft veto, because the Alvik firmware rejects every command while its
   kill-switch pin reads active. The oversight node is the only board wired
   to both paths.

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
    OverrideAssert,
    OverrideClear,
    OverrideReason,
    SupervisorHeartbeat,
    encode,
)

HEARTBEAT_TIMEOUT_MS_DEFAULT: float = 2000.0
DIGEST_CAPACITY_DEFAULT: int = 64

# Glyphs on the 12x8 LED matrix. The matrix is the only status surface on the
# board, and it is driven from the state machine, never from the link.
ANNUNCIATOR_WATCHING: str = "WATCHING"       # steady outline
ANNUNCIATOR_OVERRIDE: str = "OVERRIDE"       # solid block, latched
ANNUNCIATOR_STALE: str = "STALE"             # governance heartbeat lost
ANNUNCIATOR_ATTEST_ALERT: str = "ATTEST"     # chain gap or rollback seen


@dataclass
class SupervisorStats:
    heartbeats_received: int = 0
    digests_received: int = 0
    overrides_asserted: int = 0
    overrides_cleared: int = 0
    chain_faults: int = 0


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
    """

    def __init__(
        self,
        heartbeat_timeout_ms: float = HEARTBEAT_TIMEOUT_MS_DEFAULT,
        digest_capacity: int = DIGEST_CAPACITY_DEFAULT,
    ) -> None:
        self._timeout_s = heartbeat_timeout_ms / 1000.0
        self._capacity = digest_capacity

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
    def kill_line_asserted(self) -> bool:
        """State of the GPIO line wired into the Alvik kill-switch input.

        Asserted while an override is latched, and also before the first
        heartbeat has ever arrived. The board comes up holding the line: a
        governance tier that has not yet said anything has not yet earned the
        authority to move a robot. The line releases on first contact, with no
        latch and no arming step, and re-asserts on any override.
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
        self._write(frame)
        return True

    def start(self) -> MockR4Supervisor:
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

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _on_heartbeat(self, hb: SupervisorHeartbeat) -> None:
        with self._lock:
            self._stats.heartbeats_received += 1
            self._last_heartbeat = hb
            self._last_heartbeat_at = time.monotonic()
        self._arm_watchdog()

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
            self._write(frame)

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
