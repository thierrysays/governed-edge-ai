"""
Smoke tests for the governance filter: fast end-to-end sanity pass.

Each test exercises one complete governance path. These run first in CI
(marker: smoke) to catch catastrophic failures before the full suite.
"""

import time
from pathlib import Path

import pytest
from logger import AuditLogger

from governance.filter import DEFAULT_COMMAND_MAP, GovernanceFilter
from ipc.mock_peer import MockSTM32H5
from perception.backends import (
    NullPipeline,
    StubGestureRecognizer,
    StubObjectDetector,
    StubPoseEstimator,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def peer():
    with MockSTM32H5(watchdog_ms=10_000.0) as p:
        yield p


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "smoke_governance.db"


@pytest.fixture
def audit_logger(db_path):
    with AuditLogger(db_path) as lg:
        yield lg


@pytest.fixture
def session_id(audit_logger) -> str:
    return audit_logger.open_session(board_serial="SMOKE-GOV-001")


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
        response_timeout_s=1.0,
    )


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

@pytest.mark.smoke
def test_import_governance_filter():
    """governance.filter is importable and exports the expected symbols."""
    from governance.filter import GovernanceFilter  # noqa: F401
    assert "person" in DEFAULT_COMMAND_MAP
    assert "stop" in DEFAULT_COMMAND_MAP
    assert "proximity_breach" in DEFAULT_COMMAND_MAP


@pytest.mark.smoke
def test_full_accept_flow_object_detection(gov, audit_logger):
    """Object detector → person detection → HALT command → ACK from STM32H5."""
    pipe = StubObjectDetector(confidence=0.91)
    detections = pipe.run(None)
    gov.process_frame(detections)

    row = audit_logger._conn.execute(
        "SELECT command, command_sent, stm32_ack FROM audit_log"
    ).fetchone()
    assert row[0] == "HALT"
    assert row[1] == 1  # sent
    assert row[2] == 1  # ACK


@pytest.mark.smoke
def test_null_pipeline_no_commands(gov, audit_logger):
    """NullPipeline produces no detections → nothing logged, nothing sent."""
    pipe = NullPipeline()
    detections = pipe.run(None)
    gov.process_frame(detections)

    count = audit_logger._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert count == 0


@pytest.mark.smoke
def test_low_confidence_suppressed(gov, audit_logger):
    """Below-threshold detection is logged with command_sent=False."""
    pipe = StubObjectDetector(confidence=0.30)
    detections = pipe.run(None)
    gov.process_frame(detections)

    row = audit_logger._conn.execute(
        "SELECT command_sent, stm32_ack FROM audit_log"
    ).fetchone()
    assert row[0] == 0   # not sent
    assert row[1] is None  # no stm32_ack


@pytest.mark.smoke
def test_kill_switch_rejects_command(gov, audit_logger, peer):
    """Kill switch open → command sent but rejected by STM32H5."""
    peer.trigger_kill_switch()
    time.sleep(0.05)

    pipe = StubObjectDetector(confidence=0.91)
    detections = pipe.run(None)
    gov.process_frame(detections)

    row = audit_logger._conn.execute(
        "SELECT command_sent, stm32_ack FROM audit_log"
    ).fetchone()
    assert row[0] == 1  # Linux sent it
    assert row[1] == 0  # STM32H5 rejected


@pytest.mark.smoke
def test_all_three_stub_backends_produce_logged_commands(gov, audit_logger):
    """Each stub backend's output is accepted and logged in one composite frame."""
    detections = (
        StubObjectDetector(confidence=0.91).run(None)
        + StubGestureRecognizer(confidence=0.88).run(None)
        + StubPoseEstimator(confidence=0.76).run(None)
    )
    gov.process_frame(detections)

    count = audit_logger._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert count == 3  # all three logged

    # Only the highest-confidence detection (object/person @ 0.91) gets command_sent=True
    sent = audit_logger._conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE command_sent = 1"
    ).fetchone()[0]
    assert sent == 1


@pytest.mark.smoke
def test_log_before_act_audit_ref_valid(gov, audit_logger):
    """The audit_ref in the sent frame must be a confirmed non-zero row ID."""
    pipe = StubObjectDetector(confidence=0.91)
    detections = pipe.run(None)
    gov.process_frame(detections)

    row = audit_logger._conn.execute(
        "SELECT id, command_sent, stm32_ack FROM audit_log"
    ).fetchone()
    assert row[0] >= 1  # row ID is the audit_ref used in the IPC frame
    assert row[1] == 1  # command was sent with this audit_ref
    assert row[2] == 1  # STM32H5 ACK confirms the audit_ref was valid (≠ 0)
