"""
Smoke tests for the oversight tier (Arduino UNO R4 WiFi).

Fast, hardware-free sanity: if these fail, the end-to-end governance path is
broken and nothing downstream is worth running.
"""

import sqlite3
import time

import numpy as np
import pytest
from logger import AuditLogger

from governance.filter import GovernanceFilter
from ipc.mock_peer import MockSTM32H5
from oversight.attestation import verify_database
from oversight.mock_supervisor import MockR4Supervisor
from oversight.supervisor_link import SupervisorLink
from perception.backends import StubObjectDetector
from perception.base import DetectionResult

pytestmark = pytest.mark.smoke

SETTLE_S = 0.15
_BLANK_FRAME = np.zeros((8, 8, 3), dtype=np.uint8)


@pytest.fixture
def rig(tmp_path):
    db = tmp_path / "audit.db"
    audit_logger = AuditLogger(db)
    session_id = audit_logger.open_session()

    peer = MockSTM32H5(watchdog_ms=10_000.0).start()
    actuation = open(peer.device, "rb+", buffering=0)

    node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
    link = SupervisorLink(
        open(node.device, "rb+", buffering=0), heartbeat_interval_s=0.0
    )

    gf = GovernanceFilter(
        logger=audit_logger, session_id=session_id,
        channel=actuation, supervisor=link,
    )
    try:
        yield gf, node, link, db
    finally:
        link.close()
        node.stop()
        actuation.close()
        peer.stop()
        audit_logger.close()


def _rows(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    out = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    conn.close()
    return out


def test_oversight_modules_import():
    from oversight import attestation, mock_supervisor, supervisor_link

    assert attestation.DIGEST_BYTES == 32
    assert mock_supervisor.MockR4Supervisor is not None
    assert supervisor_link.SupervisorLink is not None


def test_accept_flow_with_oversight_attached(rig):
    """Perception to actuation, with every row witnessed off-host."""
    gf, node, _, db = rig
    detections = StubObjectDetector(confidence=0.95).run(_BLANK_FRAME)
    gf.process_frame(detections)
    time.sleep(SETTLE_S)

    row = _rows(db)[0]
    assert row["command_sent"] == 1
    assert row["stm32_ack"] == 1
    assert node.retained_digests[0][0] == row["id"]


def test_override_button_stops_actuation(rig):
    gf, node, _, db = rig
    node.press_button()
    time.sleep(SETTLE_S)
    gf.process_frame([DetectionResult("object", "person", 0.99)])

    row = _rows(db)[0]
    assert row["command_sent"] == 0
    assert node.motor_power_cut is True


def test_audit_chain_reconciles_against_the_witness(rig):
    gf, node, _, db = rig
    for _ in range(3):
        gf.process_frame([DetectionResult("object", "person", 0.91)])
    time.sleep(SETTLE_S)

    conn = sqlite3.connect(db)
    result = verify_database(conn, retained=node.retained_digests)
    conn.close()
    assert result.ok, result.reason


def test_tampering_is_detected(rig):
    """The claim the oversight node exists to support."""
    gf, node, _, db = rig
    for _ in range(3):
        gf.process_frame([DetectionResult("object", "person", 0.91)])
    time.sleep(SETTLE_S)
    witnessed = node.retained_digests

    conn = sqlite3.connect(db)
    conn.execute("UPDATE audit_log SET detection_label = 'nothing' WHERE id = 2")
    conn.commit()
    result = verify_database(conn, retained=witnessed)
    conn.close()
    assert not result.ok


def test_lost_oversight_link_fails_closed(tmp_path):
    db = tmp_path / "audit.db"
    audit_logger = AuditLogger(db)
    session_id = audit_logger.open_session()
    peer = MockSTM32H5(watchdog_ms=10_000.0).start()
    actuation = open(peer.device, "rb+", buffering=0)
    node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
    link = SupervisorLink(
        open(node.device, "rb+", buffering=0),
        heartbeat_interval_s=0.0, link_timeout_s=0.05, fail_closed=True,
    )
    gf = GovernanceFilter(
        logger=audit_logger, session_id=session_id,
        channel=actuation, supervisor=link,
    )
    try:
        node.stop()
        time.sleep(0.1)
        gf.process_frame([DetectionResult("object", "person", 0.99)])
        assert _rows(db)[0]["command_sent"] == 0
    finally:
        link.close()
        actuation.close()
        peer.stop()
        audit_logger.close()


def test_service_opens_and_closes_an_oversight_node():
    from governance.ventuno_q_service import _open_supervisor

    link, node = _open_supervisor("mock")
    try:
        assert node is not None
        assert link.poll() is False
    finally:
        link.close()
        node.stop()


def test_service_can_run_without_an_oversight_node():
    from governance.ventuno_q_service import _open_supervisor

    link, node = _open_supervisor("none")
    assert link is None
    assert node is None
