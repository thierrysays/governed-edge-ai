"""
SupervisorLink: the VENTUNO Q side of the oversight link to the UNO R4 WiFi.

Direction of authority
----------------------
Everything the VENTUNO Q sends on this link is a report. Everything the R4
sends back is an instruction. The governance tier cannot tell the oversight
node to stand down, and there is no message type that would let it: the
protocol has no OVERRIDE_DENY.

Outbound (VENTUNO Q -> R4)
    SUPERVISOR_HEARTBEAT   liveness plus session counters, every 500 ms
    ATTEST_DIGEST          the audit chain head, once per logged event
    LATCH_REQUEST          please put the relay contact here. A request only.

Inbound (R4 -> VENTUNO Q)
    OVERRIDE_ASSERT        stop issuing commands, with a reason
    OVERRIDE_CLEAR         override released
    ATTEST_ACK             the R4's live verdict on the last digest
    LATCH_REPORT           commanded, reported and observed contact position

The latch is owned by the R4 and this side can only ask. A request to close
while an override is latched is refused, and no message exists that would
change that.

Fail-safe on link loss
----------------------
`fail_closed` (default True) decides what happens when the link itself goes
away. If the R4 stops answering for `link_timeout_s`, the link reports an
override with reason GOVERNANCE_HEARTBEAT_LOST. A supervisor that cannot be
reached is not evidence that oversight is satisfied, so the default treats
silence as a veto. Set fail_closed=False only for bench work where the
oversight node is deliberately absent.
"""

from __future__ import annotations

import contextlib
import os
import select
import time
from typing import BinaryIO

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
    SystemState,
    encode,
)
from oversight.attestation import AuditChain, AuditRow

HEARTBEAT_INTERVAL_S: float = 0.5
LINK_TIMEOUT_S: float = 3.0


class SupervisorLink:
    """
    Client for the UNO R4 WiFi oversight node.

    Parameters
    ----------
    channel:
        Binary r/w channel to the R4, opened unbuffered (buffering=0). A real
        USB-C serial device in production, the MockR4Supervisor pty in tests.
    heartbeat_interval_s:
        Minimum seconds between SUPERVISOR_HEARTBEAT frames. The R4's own
        watchdog must be set well above this.
    link_timeout_s:
        Seconds without any inbound frame after which the link is considered
        lost. Only meaningful when fail_closed is True.
    fail_closed:
        True (default): a lost link counts as an active override.
    chain:
        Existing AuditChain to continue. None starts from GENESIS, which is
        correct for a fresh database.
    """

    def __init__(
        self,
        channel: BinaryIO,
        *,
        heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
        link_timeout_s: float = LINK_TIMEOUT_S,
        fail_closed: bool = True,
        chain: AuditChain | None = None,
    ) -> None:
        self._channel = channel
        self._interval = heartbeat_interval_s
        self._link_timeout = link_timeout_s
        self._fail_closed = fail_closed
        self._chain = chain if chain is not None else AuditChain()
        self._parser = FrameParser()

        self._override = False
        self._override_reason: OverrideReason | None = None
        self._last_verdict: AttestVerdict | None = None
        self._last_latch: LatchReport | None = None

        self._events_logged = 0
        self._commands_sent = 0

        now = time.monotonic()
        self._last_heartbeat_sent = 0.0
        self._last_inbound = now

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def override_active(self) -> bool:
        """True if the oversight node currently vetoes command dispatch."""
        return self._override

    @property
    def override_reason(self) -> OverrideReason | None:
        """Reason carried by the active override, or None when clear."""
        return self._override_reason

    @property
    def last_verdict(self) -> AttestVerdict | None:
        """Most recent ATTEST_ACK verdict from the oversight node."""
        return self._last_verdict

    @property
    def last_latch(self) -> LatchReport | None:
        """Most recent LATCH_REPORT from the arbiter, or None."""
        return self._last_latch

    @property
    def motors_isolated(self) -> bool:
        """True only when the arbiter has *observed* the contact open.

        False before any report arrives and false when the observation is
        UNKNOWN. This side never infers isolation it has not been shown.
        """
        return (
            self._last_latch is not None
            and self._last_latch.observed is LatchPosition.OPEN
        )

    @property
    def chain_head(self) -> bytes:
        """Current audit chain head."""
        return self._chain.head

    @property
    def chain(self) -> AuditChain:
        return self._chain

    @property
    def link_alive(self) -> bool:
        """False once nothing has been heard from the R4 for link_timeout_s."""
        return not self._link_is_stale()

    @property
    def events_logged(self) -> int:
        return self._events_logged

    @property
    def commands_sent(self) -> int:
        return self._commands_sent

    # ------------------------------------------------------------------
    # Public API used by GovernanceFilter
    # ------------------------------------------------------------------

    def poll(self) -> bool:
        """
        Drain inbound frames, update override state, return override_active.

        Non-blocking. Safe to call on every frame; the governance filter calls
        it before deciding whether a command may be transmitted.
        """
        self._drain()
        if self._fail_closed and self._link_is_stale():
            self._raise_override(OverrideReason.GOVERNANCE_HEARTBEAT_LOST)
        return self._override

    def record(self, row: AuditRow, *, command_sent: bool = False) -> bytes:
        """
        Fold one audit row into the chain, publish the new head, and heartbeat
        if one is due. Returns the new chain head.

        Called once per logged event, after log_event() has returned a
        confirmed audit_ref. Transport failures are swallowed: the oversight
        node going dark must never take the governance service down with it,
        and poll() converts the silence into an override on the next frame.

        A row that does not follow the chain (a repeated or rewound audit_ref)
        is an attestation fault detected on this side rather than at the node.
        It raises the override locally and publishes nothing: forwarding a
        digest that does not follow would corrupt the witness's record of what
        it saw. The head is returned unchanged.
        """
        try:
            head = self._chain.append(row)
        except ValueError:
            self._raise_override(OverrideReason.ATTESTATION_MISMATCH)
            return self._chain.head
        self._events_logged += 1
        if command_sent:
            self._commands_sent += 1
        self._send(encode(AttestDigest(audit_ref=row.audit_ref, digest=head)))
        self.heartbeat()
        return head

    def request_latch(self, desired: LatchPosition, *, audit_ref: int = 0) -> None:
        """Ask the arbiter to move the contact. It may refuse.

        Requesting OPEN is the governance tier asking for its own decision to
        be enforced physically, which the arbiter always honours. Requesting
        CLOSED is asking to be let go, which it honours only when nothing is
        latched. Both are requests: this side cannot move the relay.
        """
        self._send(encode(LatchRequest(audit_ref=audit_ref, desired=desired)))

    def heartbeat(self, *, system_state: SystemState = SystemState.ARMED,
                  force: bool = False) -> bool:
        """
        Send a SUPERVISOR_HEARTBEAT if the interval has elapsed.

        Returns True if a frame was written. `force` sends unconditionally.
        """
        now = time.monotonic()
        if not force and (now - self._last_heartbeat_sent) < self._interval:
            return False
        self._last_heartbeat_sent = now
        self._send(encode(SupervisorHeartbeat(
            last_audit_ref=self._chain.last_ref,
            system_state=system_state,
            events_logged=self._events_logged,
            commands_sent=self._commands_sent,
        )))
        return True

    def close(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            self._channel.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _link_is_stale(self) -> bool:
        return (time.monotonic() - self._last_inbound) > self._link_timeout

    def _raise_override(self, reason: OverrideReason) -> None:
        self._override = True
        self._override_reason = reason

    def _drain(self) -> None:
        """Read whatever is pending on the channel without blocking."""
        try:
            fd = self._channel.fileno()
        except (OSError, ValueError):
            return

        while True:
            try:
                ready, _, _ = select.select([fd], [], [], 0)
            except (OSError, ValueError):
                return
            if not ready:
                return
            try:
                data = os.read(fd, 1024)
            except OSError:
                return
            if not data:
                return
            self._last_inbound = time.monotonic()
            self._parser.feed(data)
            for msg in self._parser.pop_messages():
                self._dispatch(msg)

    def _dispatch(self, msg: object) -> None:
        if isinstance(msg, OverrideAssert):
            self._raise_override(msg.reason)
        elif isinstance(msg, OverrideClear):
            self._override = False
            self._override_reason = None
        elif isinstance(msg, AttestAck):
            self._last_verdict = msg.verdict
            if msg.verdict != AttestVerdict.CHAIN_OK:
                self._raise_override(OverrideReason.ATTESTATION_MISMATCH)
        elif isinstance(msg, LatchReport):
            self._last_latch = msg
            # A contact that is not where it was told to be is a fault in
            # either direction, so this side stops issuing commands too. The
            # arbiter has its own override for the same reading; both firing
            # is correct, since neither is allowed to rely on the other.
            if not msg.agrees:
                self._raise_override(OverrideReason.LATCH_MISMATCH)

    def _send(self, frame: bytes) -> None:
        with contextlib.suppress(OSError, ValueError):
            self._channel.write(frame)
