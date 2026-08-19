"""
GovernanceFilter: perception → audit log → IPC command dispatch.

Governance contract (enforced here, not trusted from upstream):

  1. Log-before-act: audit_ref is obtained from logger.log_event() before
     any CommandRequest frame is transmitted to the STM32H5.
  2. No log → no command: if log_event() raises, the exception propagates
     and no command frame is sent.
  3. Confidence gate (Linux side): detections below threshold are logged
     with command_sent=False. No command frame is sent for suppressed
     detections; the suppression is on record for forensic analysis.
  4. One command per frame: the highest-confidence detection that passes
     the threshold is selected for transmission. All remaining detections
     are logged with command_sent=False regardless of their confidence.
  5. Dual-layer confidence gate: the Linux filter and the STM32H5 each
     enforce their own threshold independently (defence-in-depth).
  6. ACK/REJECT tracking: update_stm32_ack() is called exactly once per
     transmitted command. A response timeout leaves stm32_ack NULL in the
     audit log. A write that fails outright leaves stm32_ack NULL and flags
     the event: the row already claims command_sent=1 and the log is
     append-only, so the flag is the only honest way to record that the
     frame never reached the wire.
  7. Oversight veto: when a SupervisorLink is attached, the UNO R4 WiFi
     oversight node is polled before any command is transmitted. While its
     override is asserted, every detection is logged with command_sent=False
     and the reason recorded in notes. The veto is one-directional: this
     filter can neither clear the override nor tell the oversight node to
     stand down.
  8. Witness-before-act: each logged row is read back from SQLite, folded
     into the attestation hash chain, and the new chain head is published to
     the oversight node before the CommandRequest frame is written. The
     independent witness therefore holds a digest covering the audit row
     before the actuator ever sees the command.

     Limit worth stating: the filter does not block on the node's ATTEST_ACK
     before transmitting. A verdict of GAP or CHAIN_BREAK raises the override
     and stops the next command, not the one in flight. Blocking on the ack
     would put the oversight link inside the actuation latency budget, which
     is the wrong trade for a link whose job is to observe.

Runtime dependency:
  The audit-service logger module (audit-service/logger.py) must be on
  sys.path. In production this is set by the launch script; in tests it
  is set by linux-stack/tests/conftest.py.
"""

from __future__ import annotations

import os
import select
import time
from typing import BinaryIO

from logger import AuditEvent, AuditLogger  # audit-service must be on sys.path

from ipc.codec import (
    ActionType,
    Actor,
    CommandAck,
    CommandReject,
    CommandRequest,
    FrameParser,
    encode,
)
from oversight.attestation import AuditRow
from oversight.supervisor_link import SupervisorLink
from perception.base import DetectionResult

# ---------------------------------------------------------------------------
# Default label → (ActionType, action_param) mapping
# ---------------------------------------------------------------------------

# Safety-conservative fallback: anything not explicitly mapped triggers HALT.
_DEFAULT_ACTION: tuple[ActionType, int] = (ActionType.HALT, 0)

DEFAULT_COMMAND_MAP: dict[str, tuple[ActionType, int]] = {
    # Object detections: any person or obstacle in path → emergency halt
    "person":           (ActionType.HALT, 0),
    "robot_part":       (ActionType.HALT, 0),
    "tool":             (ActionType.HALT, 0),
    # Gesture commands for Alvik mobile robot
    "stop":             (ActionType.HALT, 0),
    "thumbs_up":        (ActionType.MOVE_FORWARD,  50),   # param: speed (%)
    "thumbs_down":      (ActionType.MOVE_BACKWARD, 50),
    "swipe_left":       (ActionType.TURN_LEFT,     45),   # param: degrees
    "swipe_right":      (ActionType.TURN_RIGHT,    45),
    # Pose detections: proximity breach → emergency halt
    "proximity_breach": (ActionType.HALT, 0),
}


# ---------------------------------------------------------------------------
# GovernanceFilter
# ---------------------------------------------------------------------------

class GovernanceFilter:
    """
    Central safety gate bridging the perception pipeline to IPC command dispatch.

    Parameters
    ----------
    logger:
        AuditLogger instance (from audit-service/logger.py).
    session_id:
        Open session ID from logger.open_session().
    channel:
        Binary r/w channel to the STM32H5. Must be opened in unbuffered
        binary mode (buffering=0) so writes reach the OS immediately.
        Works with a real serial port or the mock pty from MockSTM32H5.
    confidence_threshold:
        Minimum confidence to pass the Linux-side gate. Matches the
        STM32H5 dual gate default (0.70).
    command_map:
        Override the label → (ActionType, action_param) mapping. Labels
        absent from the map fall back to _DEFAULT_ACTION (HALT).
    response_timeout_s:
        Maximum seconds to wait for CommandAck/CommandReject after sending
        a CommandRequest. Timeout leaves stm32_ack NULL in the audit log.
    supervisor:
        Optional SupervisorLink to the UNO R4 WiFi oversight node. When
        present, its override vetoes command dispatch and every logged row is
        chained and published to it. None runs the three-board configuration
        with no independent oversight tier, which is the weaker arrangement
        the R4 exists to correct.
    """

    def __init__(
        self,
        logger: AuditLogger,
        session_id: str,
        channel: BinaryIO,
        *,
        confidence_threshold: float = 0.70,
        command_map: dict[str, tuple[ActionType, int]] | None = None,
        response_timeout_s: float = 0.5,
        supervisor: SupervisorLink | None = None,
    ) -> None:
        self._logger = logger
        self._session_id = session_id
        self._channel = channel
        self._threshold = confidence_threshold
        self._command_map = command_map if command_map is not None else DEFAULT_COMMAND_MAP
        self._timeout = response_timeout_s
        self._supervisor = supervisor
        self._t0_us = int(time.monotonic() * 1_000_000)
        self._parser = FrameParser()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_frame(self, detections: list[DetectionResult]) -> None:
        """
        Process one captured frame's worth of detections.

        Must be called from a single thread (not re-entrant).
        Does nothing if detections is empty.
        """
        if not detections:
            return

        # Ask the oversight node first. Its answer gates every detection in
        # this frame, and it is asked before anything is logged so that the
        # veto is on record against the same rows it suppressed.
        override_note = self._oversight_note()

        # Highest-confidence detection first: it is the only candidate for
        # command dispatch; all others are logged as suppressed.
        by_confidence = sorted(detections, key=lambda d: d.confidence, reverse=True)

        command_sent_this_frame = False
        for detection in by_confidence:
            action_type, action_param = self._command_map.get(
                detection.label, _DEFAULT_ACTION
            )
            should_send = (
                override_note is None
                and not command_sent_this_frame
                and detection.passes_threshold(self._threshold)
            )

            # Governance invariant: log-before-act.
            # audit_ref must be non-zero; log_event() guarantees this by
            # returning the SQLite rowid (≥ 1).
            audit_ref = self._logger.log_event(AuditEvent(
                session_id=self._session_id,
                actor="ai",
                detection_type=detection.detection_type,
                detection_label=detection.label,
                confidence=detection.confidence,
                command=action_type.name,
                command_sent=should_send,
                stm32_ack=None,
                notes=override_note,
            ))

            # Witness-before-act: the oversight node holds a digest covering
            # this row before the command frame is written.
            self._witness(audit_ref, command_sent=should_send)

            if should_send:
                command_sent_this_frame = True
                ack = self._send_command(detection, action_type, action_param, audit_ref)
                if ack is not None:
                    self._logger.update_stm32_ack(audit_ref, ack)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _oversight_note(self) -> str | None:
        """Poll the oversight node. Returns a note to record, or None if clear."""
        if self._supervisor is None:
            return None
        if not self._supervisor.poll():
            return None
        reason = self._supervisor.override_reason
        label = reason.name if reason is not None else "UNSPECIFIED"
        return f"suppressed: oversight override active ({label})"

    def _witness(self, audit_ref: int, *, command_sent: bool) -> None:
        """Fold the stored row into the attestation chain and publish the head.

        The row is read back from SQLite rather than reconstructed from the
        AuditEvent: the chain must commit to what the database holds, not to
        what this process believes it wrote.
        """
        if self._supervisor is None:
            return
        row = self._logger.fetch_event(audit_ref)
        if row is None:  # pragma: no cover - log_event just returned this id
            return
        self._supervisor.record(AuditRow.from_mapping(row), command_sent=command_sent)

    @property
    def chain_head(self) -> bytes | None:
        """Current attestation chain head, or None with no oversight node."""
        return None if self._supervisor is None else self._supervisor.chain_head

    def _send_command(
        self,
        detection: DetectionResult,
        action_type: ActionType,
        action_param: int,
        audit_ref: int,
    ) -> bool | None:
        """Encode and write a CommandRequest frame, then wait for a response."""
        # timestamp_us wraps at uint32 max (~71 min of session time)
        timestamp_us = (int(time.monotonic() * 1_000_000) - self._t0_us) & 0xFFFFFFFF
        frame = encode(CommandRequest(
            audit_ref=audit_ref,
            timestamp_us=timestamp_us,
            actor=Actor.AI,
            confidence=detection.confidence,
            action_type=action_type,
            action_param=action_param,
        ))
        try:
            self._channel.write(frame)
        except OSError as exc:
            # The row already says command_sent=1, and the log is append-only,
            # so the record cannot be withdrawn. Flag it instead: an event
            # marked for review is the honest way to say the frame was
            # composed and never reached the wire.
            self._logger.flag_event(
                audit_ref, notes=f"transmit failed: {exc.__class__.__name__}: {exc}"
            )
            return None
        return self._read_response(audit_ref)

    def _read_response(self, audit_ref: int) -> bool | None:
        """
        Poll the channel until CommandAck/CommandReject matching audit_ref.

        Returns True on ACK, False on REJECT, None on timeout or I/O error.
        Unrelated message types (HeartbeatAck, StatusResponse, HaltNotify)
        are fed into the parser for later retrieval and skipped here.
        """
        fd = self._channel.fileno()
        deadline = time.monotonic() + self._timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            try:
                ready, _, _ = select.select([fd], [], [], min(remaining, 0.05))
            except (OSError, ValueError):
                return None

            if not ready:
                continue

            try:
                data = os.read(fd, 512)
            except OSError:
                return None

            if not data:
                return None

            self._parser.feed(data)
            for msg in self._parser.pop_messages():
                if isinstance(msg, CommandAck) and msg.audit_ref == audit_ref:
                    return True
                if isinstance(msg, CommandReject) and msg.audit_ref == audit_ref:
                    return False
