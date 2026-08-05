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
     audit log.

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
from perception.base import DetectionResult

# ---------------------------------------------------------------------------
# Default label → (ActionType, action_param) mapping
# ---------------------------------------------------------------------------

# Safety-conservative fallback: anything not explicitly mapped triggers HALT.
_DEFAULT_ACTION: tuple[ActionType, int] = (ActionType.HALT, 0)

DEFAULT_COMMAND_MAP: dict[str, tuple[ActionType, int]] = {
    # Object detections → safety halt (person or equipment in workspace)
    "person":           (ActionType.HALT, 0),
    "robot_part":       (ActionType.HALT, 0),
    "tool":             (ActionType.HALT, 0),
    # Gesture commands
    "stop":             (ActionType.HALT, 0),
    "thumbs_up":        (ActionType.GRIPPER_OPEN, 0),
    "thumbs_down":      (ActionType.GRIPPER_CLOSE, 0),
    # Pose detections → safety halt
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
    ) -> None:
        self._logger = logger
        self._session_id = session_id
        self._channel = channel
        self._threshold = confidence_threshold
        self._command_map = command_map if command_map is not None else DEFAULT_COMMAND_MAP
        self._timeout = response_timeout_s
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

        # Highest-confidence detection first — it is the only candidate for
        # command dispatch; all others are logged as suppressed.
        by_confidence = sorted(detections, key=lambda d: d.confidence, reverse=True)

        command_sent_this_frame = False
        for detection in by_confidence:
            action_type, action_param = self._command_map.get(
                detection.label, _DEFAULT_ACTION
            )
            should_send = (
                not command_sent_this_frame
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
            ))

            if should_send:
                command_sent_this_frame = True
                ack = self._send_command(detection, action_type, action_param, audit_ref)
                if ack is not None:
                    self._logger.update_stm32_ack(audit_ref, ack)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
        self._channel.write(frame)
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
