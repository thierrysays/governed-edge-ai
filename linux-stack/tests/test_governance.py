"""
Unit tests for linux-stack/governance/filter.py.

Covers: default command map, confidence gating (both sides of threshold),
log-before-act ordering, one-command-per-frame selection, multi-detection
frame handling, ACK/REJECT recording, kill-switch rejection, unknown labels,
and the NULL-stm32_ack timeout path.

Fixtures wire the real MockSTM32H5 peer and AuditLogger so the full
perception → governance → IPC → audit round-trip is exercised.
"""

import time
from pathlib import Path

import pytest
from logger import AuditLogger

from governance.filter import DEFAULT_COMMAND_MAP, GovernanceFilter
from ipc.codec import ActionType
from ipc.mock_peer import MockSTM32H5
from perception.base import DetectionResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def peer():
    with MockSTM32H5(watchdog_ms=10_000.0) as p:
        yield p


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "governance_test.db"


@pytest.fixture
def audit_logger(db_path):
    with AuditLogger(db_path) as lg:
        yield lg


@pytest.fixture
def session_id(audit_logger) -> str:
    return audit_logger.open_session(board_serial="TEST-GOV-001")


@pytest.fixture
def channel(peer):
    ch = open(peer.device, "rb+", buffering=0)  # noqa: SIM115
    yield ch
    ch.close()


@pytest.fixture
def gov(audit_logger, session_id, channel):
    return GovernanceFilter(
        logger=audit_logger,
        session_id=session_id,
        channel=channel,
        confidence_threshold=0.70,
        response_timeout_s=1.0,
    )


def _det(label: str = "person", confidence: float = 0.91, dt: str = "object") -> DetectionResult:
    return DetectionResult(detection_type=dt, label=label, confidence=confidence)  # type: ignore[arg-type]


def _log_count(audit_logger: AuditLogger) -> int:
    return audit_logger._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]


def _log_row(audit_logger: AuditLogger, *, n: int = 1) -> tuple:
    rows = audit_logger._conn.execute(
        "SELECT id, command, command_sent, stm32_ack, detection_label, confidence"
        " FROM audit_log ORDER BY id"
    ).fetchmany(n)
    return rows[0] if n == 1 else tuple(rows)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Empty frame
# ---------------------------------------------------------------------------

class TestEmptyFrame:
    def test_no_detections_no_log_entries(self, gov, audit_logger):
        gov.process_frame([])
        assert _log_count(audit_logger) == 0

    def test_no_detections_returns_none(self, gov):
        result = gov.process_frame([])
        assert result is None  # process_frame always returns None


# ---------------------------------------------------------------------------
# Confidence gate (Linux side)
# ---------------------------------------------------------------------------

class TestConfidenceGate:
    def test_below_threshold_logged_not_sent(self, gov, audit_logger):
        gov.process_frame([_det(confidence=0.50)])
        row = _log_row(audit_logger)
        assert row[2] == 0    # command_sent = False
        assert row[3] is None  # stm32_ack = NULL

    def test_at_threshold_logged_and_sent(self, gov, audit_logger):
        # 0.70 is the threshold; values exactly at it should be accepted by the
        # Linux gate. Float32 encoding of 0.70 is 0.6999…, which the STM32H5 mock
        # rejects, so stm32_ack=False here — but command_sent must be True.
        gov.process_frame([_det(confidence=0.70)])
        row = _log_row(audit_logger)
        assert row[2] == 1    # command_sent = True (Linux gate passed)

    def test_above_threshold_logged_and_sent(self, gov, audit_logger):
        gov.process_frame([_det(confidence=0.91)])
        row = _log_row(audit_logger)
        assert row[2] == 1    # command_sent = True

    def test_above_threshold_stm32_ack_true(self, gov, audit_logger):
        gov.process_frame([_det(confidence=0.91)])
        row = _log_row(audit_logger)
        assert row[3] == 1    # ACK from mock peer

    def test_confidence_boundary_0_69_suppressed(self, gov, audit_logger):
        gov.process_frame([_det(confidence=0.69)])
        row = _log_row(audit_logger)
        assert row[2] == 0    # below threshold — not sent

    def test_confidence_1_0_accepted(self, gov, audit_logger):
        gov.process_frame([_det(confidence=0.9999)])  # clamped by DetectionResult
        row = _log_row(audit_logger)
        assert row[2] == 1    # sent


# ---------------------------------------------------------------------------
# Command mapping
# ---------------------------------------------------------------------------

class TestCommandMapping:
    def test_person_maps_to_halt(self, gov, audit_logger):
        gov.process_frame([_det("person", 0.91, "object")])
        assert _log_row(audit_logger)[1] == "HALT"

    def test_stop_gesture_maps_to_halt(self, gov, audit_logger):
        gov.process_frame([_det("stop", 0.88, "gesture")])
        assert _log_row(audit_logger)[1] == "HALT"

    def test_proximity_breach_maps_to_halt(self, gov, audit_logger):
        gov.process_frame([_det("proximity_breach", 0.76, "pose")])
        assert _log_row(audit_logger)[1] == "HALT"

    def test_thumbs_up_maps_to_gripper_open(self, gov, audit_logger):
        gov.process_frame([_det("thumbs_up", 0.88, "gesture")])
        assert _log_row(audit_logger)[1] == "GRIPPER_OPEN"

    def test_thumbs_down_maps_to_gripper_close(self, gov, audit_logger):
        gov.process_frame([_det("thumbs_down", 0.88, "gesture")])
        assert _log_row(audit_logger)[1] == "GRIPPER_CLOSE"

    def test_robot_part_maps_to_halt(self, gov, audit_logger):
        gov.process_frame([_det("robot_part", 0.91, "object")])
        assert _log_row(audit_logger)[1] == "HALT"

    def test_tool_maps_to_halt(self, gov, audit_logger):
        gov.process_frame([_det("tool", 0.91, "object")])
        assert _log_row(audit_logger)[1] == "HALT"

    def test_unknown_label_defaults_to_halt(self, gov, audit_logger):
        gov.process_frame([_det("mystery_object", 0.91, "object")])
        assert _log_row(audit_logger)[1] == "HALT"

    def test_all_default_map_entries_present(self):
        expected = {"person", "robot_part", "tool", "stop", "thumbs_up",
                    "thumbs_down", "proximity_breach"}
        assert expected.issubset(set(DEFAULT_COMMAND_MAP.keys()))

    def test_custom_command_map(self, audit_logger, session_id, channel):
        custom_map = {"wave": (ActionType.MOVE_JOINT_1, 50)}
        gov = GovernanceFilter(
            logger=audit_logger, session_id=session_id, channel=channel,
            command_map=custom_map, response_timeout_s=1.0,
        )
        gov.process_frame([_det("wave", 0.91, "gesture")])
        assert _log_row(audit_logger)[1] == "MOVE_JOINT_1"

    def test_custom_map_unknown_still_defaults_to_halt(self, audit_logger, session_id, channel):
        gov = GovernanceFilter(
            logger=audit_logger, session_id=session_id, channel=channel,
            command_map={}, response_timeout_s=1.0,
        )
        gov.process_frame([_det("person", 0.91, "object")])
        assert _log_row(audit_logger)[1] == "HALT"


# ---------------------------------------------------------------------------
# Multi-detection frame
# ---------------------------------------------------------------------------

class TestMultiDetectionFrame:
    def _rows(self, audit_logger: AuditLogger) -> list[dict]:
        cols = ["id", "command", "command_sent", "stm32_ack", "detection_label", "confidence"]
        raw = audit_logger._conn.execute(
            "SELECT id, command, command_sent, stm32_ack, detection_label, confidence"
            " FROM audit_log ORDER BY id"
        ).fetchall()
        return [dict(zip(cols, row, strict=True)) for row in raw]

    def test_all_detections_logged(self, gov, audit_logger):
        gov.process_frame([
            _det("person", 0.91),
            _det("proximity_breach", 0.76, "pose"),
            _det("tool", 0.55),
        ])
        assert _log_count(audit_logger) == 3

    def test_only_highest_confidence_gets_command_sent(self, gov, audit_logger):
        gov.process_frame([
            _det("person", 0.91),
            _det("proximity_breach", 0.76, "pose"),
        ])
        rows = self._rows(audit_logger)
        # Should be ordered by confidence (highest first in the log)
        by_label = {r["detection_label"]: r for r in rows}
        assert by_label["person"]["command_sent"] == 1         # highest → sent
        assert by_label["proximity_breach"]["command_sent"] == 0  # second → suppressed

    def test_second_above_threshold_not_sent(self, gov, audit_logger):
        gov.process_frame([
            _det("person", 0.91),
            _det("proximity_breach", 0.76, "pose"),  # above threshold but not first
        ])
        rows = self._rows(audit_logger)
        by_label = {r["detection_label"]: r for r in rows}
        assert by_label["proximity_breach"]["command_sent"] == 0

    def test_below_threshold_detection_logged_no_command_sent(self, gov, audit_logger):
        gov.process_frame([
            _det("person", 0.91),
            _det("tool", 0.40),
        ])
        rows = self._rows(audit_logger)
        by_label = {r["detection_label"]: r for r in rows}
        assert by_label["tool"]["command_sent"] == 0
        assert by_label["tool"]["stm32_ack"] is None

    def test_all_below_threshold_no_commands_sent(self, gov, audit_logger):
        gov.process_frame([
            _det("person", 0.30),
            _det("tool", 0.25),
        ])
        rows = self._rows(audit_logger)
        assert all(r["command_sent"] == 0 for r in rows)
        assert all(r["stm32_ack"] is None for r in rows)

    def test_frame_sorted_by_confidence_descending(self, gov, audit_logger):
        # Input order is deliberately different from confidence order
        gov.process_frame([
            _det("tool", 0.55),
            _det("person", 0.91),        # highest
            _det("proximity_breach", 0.76, "pose"),
        ])
        rows = self._rows(audit_logger)
        # Log entries ordered by insert (= confidence descending order)
        assert rows[0]["detection_label"] == "person"           # 0.91
        assert rows[1]["detection_label"] == "proximity_breach" # 0.76
        assert rows[2]["detection_label"] == "tool"             # 0.55


# ---------------------------------------------------------------------------
# Log-before-act integrity
# ---------------------------------------------------------------------------

class TestLogBeforeAct:
    def test_audit_ref_is_nonzero(self, gov, audit_logger):
        gov.process_frame([_det(confidence=0.91)])
        row = _log_row(audit_logger)
        assert row[0] >= 1  # SQLite rowid is always ≥ 1

    def test_stm32_ack_set_after_process_frame(self, gov, audit_logger):
        # stm32_ack is set synchronously before process_frame returns
        gov.process_frame([_det(confidence=0.91)])
        row = _log_row(audit_logger)
        assert row[3] == 1  # stm32_ack updated to True

    def test_multiple_frames_increment_ids(self, gov, audit_logger):
        gov.process_frame([_det(confidence=0.91)])
        gov.process_frame([_det(confidence=0.91)])
        rows = audit_logger._conn.execute(
            "SELECT id FROM audit_log ORDER BY id"
        ).fetchall()
        ids = [r[0] for r in rows]
        assert ids == list(range(1, len(ids) + 1))

    def test_suppressed_detection_logged_with_audit_ref(self, gov, audit_logger):
        gov.process_frame([_det(confidence=0.30)])
        assert _log_count(audit_logger) == 1  # logged even though suppressed

    def test_actor_is_ai(self, gov, audit_logger):
        gov.process_frame([_det(confidence=0.91)])
        actor = audit_logger._conn.execute(
            "SELECT actor FROM audit_log"
        ).fetchone()[0]
        assert actor == "ai"

    def test_session_id_recorded(self, gov, audit_logger, session_id):
        gov.process_frame([_det(confidence=0.91)])
        sid = audit_logger._conn.execute(
            "SELECT session_id FROM audit_log"
        ).fetchone()[0]
        assert sid == session_id


# ---------------------------------------------------------------------------
# STM32H5 reject paths
# ---------------------------------------------------------------------------

class TestRejectPaths:
    def test_kill_switch_rejection_stm32_ack_false(self, gov, audit_logger, peer):
        peer.trigger_kill_switch()
        time.sleep(0.05)  # wait for mock peer reader thread to process

        gov.process_frame([_det(confidence=0.91)])
        row = _log_row(audit_logger)
        assert row[2] == 1  # command_sent = True (Linux sent it)
        assert row[3] == 0  # stm32_ack = False (MCU rejected)

    def test_low_confidence_float32_round_trip_rejected_by_stm32(self, gov, audit_logger):
        # 0.70 in float64 encodes to slightly-below-0.70 in float32.
        # The mock peer (dual gate) rejects it; the Linux gate passed it.
        gov.process_frame([_det(confidence=0.70)])
        row = _log_row(audit_logger)
        assert row[2] == 1  # sent by Linux
        assert row[3] == 0  # rejected by STM32H5 (float32 rounding)

    @pytest.mark.regression
    def test_command_sent_false_never_updates_stm32_ack(self, gov, audit_logger):
        # Suppressed detections must not update stm32_ack — they were never sent.
        gov.process_frame([_det(confidence=0.30)])
        row = _log_row(audit_logger)
        assert row[3] is None  # stm32_ack stays NULL

    def test_gripper_open_accepted_by_stm32(self, gov, audit_logger):
        gov.process_frame([_det("thumbs_up", 0.88, "gesture")])
        row = _log_row(audit_logger)
        assert row[3] == 1  # ACK for GRIPPER_OPEN


# ---------------------------------------------------------------------------
# Timeout path (no peer response)
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_timeout_leaves_stm32_ack_null(self, audit_logger, session_id, tmp_path):
        # Write-only pipe simulates a channel that never sends a response.
        import os
        rfd, wfd = os.pipe()
        wfile = open(wfd, "wb", buffering=0)  # noqa: SIM115

        gov = GovernanceFilter(
            logger=audit_logger,
            session_id=session_id,
            channel=wfile,  # type: ignore[arg-type]
            confidence_threshold=0.70,
            response_timeout_s=0.05,  # short timeout for test speed
        )
        try:
            gov.process_frame([_det(confidence=0.91)])
        finally:
            wfile.close()
            os.close(rfd)

        row = _log_row(audit_logger)
        assert row[2] == 1   # command_sent = True
        assert row[3] is None  # stm32_ack = NULL (timeout)
