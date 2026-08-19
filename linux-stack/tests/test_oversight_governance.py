"""
Integration tests for GovernanceFilter with an oversight node attached.

Covers invariants 7 (oversight veto) and 8 (witness-before-act) from the
governance contract in governance/filter.py, end to end: perception ->
audit log -> attestation chain -> UNO R4 WiFi -> Alvik IPC.

Every test runs against a real audit database, a real MockSTM32H5 pty for
the actuation link and a real MockR4Supervisor pty for the oversight link.
"""

import sqlite3
import time

import pytest
from logger import AuditEvent, AuditLogger

from governance.filter import GovernanceFilter
from ipc.mock_peer import MockSTM32H5
from oversight.attestation import AuditChain, read_rows, verify_database
from oversight.mock_supervisor import MockR4Supervisor
from oversight.supervisor_link import SupervisorLink
from perception.base import DetectionResult

SETTLE_S = 0.15


def det(label: str = "person", confidence: float = 0.91,
        detection_type: str = "object") -> DetectionResult:
    return DetectionResult(
        detection_type=detection_type, label=label,
        confidence=confidence, backend="test",
    )


@pytest.fixture
def rig(tmp_path):
    """A governance filter wired to both a mock Alvik and a mock oversight node."""
    db = tmp_path / "audit.db"
    audit_logger = AuditLogger(db)
    session_id = audit_logger.open_session(board_serial="RIG")

    peer = MockSTM32H5(watchdog_ms=10_000.0).start()
    actuation = open(peer.device, "rb+", buffering=0)

    node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
    oversight = open(node.device, "rb+", buffering=0)
    link = SupervisorLink(oversight, heartbeat_interval_s=0.0)

    gf = GovernanceFilter(
        logger=audit_logger,
        session_id=session_id,
        channel=actuation,
        response_timeout_s=0.5,
        supervisor=link,
    )
    try:
        yield gf, node, link, audit_logger, db
    finally:
        link.close()
        node.stop()
        actuation.close()
        peer.stop()
        audit_logger.close()


def rows(db) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    out = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    conn.close()
    return out


# ---------------------------------------------------------------------------
# Invariant 7: oversight veto
# ---------------------------------------------------------------------------

class TestOversightVeto:
    def test_command_flows_while_the_node_is_watching(self, rig):
        gf, node, _, _, db = rig
        gf.process_frame([det(confidence=0.91)])
        row = rows(db)[0]
        assert row["command_sent"] == 1
        assert row["stm32_ack"] == 1
        assert row["notes"] is None
        assert node.override_active is False

    def test_button_press_suppresses_the_command(self, rig):
        gf, node, _, _, db = rig
        node.press_button()
        time.sleep(SETTLE_S)
        gf.process_frame([det(confidence=0.99)])
        row = rows(db)[0]
        assert row["command_sent"] == 0
        assert row["stm32_ack"] is None

    def test_suppression_reason_is_on_record(self, rig):
        gf, node, _, _, db = rig
        node.press_button()
        time.sleep(SETTLE_S)
        gf.process_frame([det()])
        assert "OPERATOR_BUTTON" in rows(db)[0]["notes"]

    def test_detection_is_still_logged_under_an_override(self, rig):
        """The veto stops the command, never the record of what was seen."""
        gf, node, _, _, db = rig
        node.press_button()
        time.sleep(SETTLE_S)
        gf.process_frame([det("person", 0.95), det("tool", 0.80)])
        logged = rows(db)
        assert len(logged) == 2
        assert [r["detection_label"] for r in logged] == ["person", "tool"]
        assert all(r["command_sent"] == 0 for r in logged)

    def test_command_resumes_after_the_override_clears(self, rig):
        gf, node, link, _, db = rig
        link.heartbeat(force=True)
        time.sleep(SETTLE_S)

        node.press_button()
        time.sleep(SETTLE_S)
        gf.process_frame([det()])

        node.release_button()
        assert node.clear_override() is True
        time.sleep(SETTLE_S)
        gf.process_frame([det()])

        logged = rows(db)
        assert logged[0]["command_sent"] == 0
        assert logged[1]["command_sent"] == 1

    def test_attestation_gap_stops_the_next_command(self, rig):
        """Rows written outside the governed path leave a gap in the digest
        stream. The node sees it and vetoes the next frame.

        The documented limit shows here too: it stops the following command,
        it does not recall the one already sent."""
        gf, node, link, audit_logger, db = rig
        gf.process_frame([det()])                 # audit_ref 1, witnessed
        time.sleep(SETTLE_S)

        # Two rows the oversight node never sees: an out-of-band writer, or a
        # governance process that logged without publishing.
        for _ in range(2):
            audit_logger.log_event(AuditEvent(
                session_id=gf._session_id,        # noqa: SLF001
                actor="ai", detection_type="object", detection_label="ghost",
                confidence=0.50, command="HALT", command_sent=False,
            ))

        gf.process_frame([det()])                 # audit_ref 4: a gap at the node
        time.sleep(SETTLE_S)
        gf.process_frame([det()])                 # vetoed

        logged = rows(db)
        assert logged[0]["command_sent"] == 1     # before the gap
        assert logged[-1]["command_sent"] == 0    # after the node noticed
        assert "ATTESTATION_MISMATCH" in logged[-1]["notes"]
        assert node.stats.chain_faults >= 1

    def test_lost_oversight_link_fails_closed(self, tmp_path):
        """Silence from the oversight node is a veto, not a clearance."""
        db = tmp_path / "audit.db"
        audit_logger = AuditLogger(db)
        session_id = audit_logger.open_session()
        peer = MockSTM32H5(watchdog_ms=10_000.0).start()
        actuation = open(peer.device, "rb+", buffering=0)

        node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
        oversight = open(node.device, "rb+", buffering=0)
        link = SupervisorLink(
            oversight, heartbeat_interval_s=0.0, link_timeout_s=0.05, fail_closed=True
        )
        gf = GovernanceFilter(
            logger=audit_logger, session_id=session_id,
            channel=actuation, supervisor=link,
        )
        try:
            node.stop()          # the oversight node goes away
            time.sleep(0.1)
            gf.process_frame([det(confidence=0.99)])
            row = rows(db)[0]
            assert row["command_sent"] == 0
            assert "GOVERNANCE_HEARTBEAT_LOST" in row["notes"]
        finally:
            link.close()
            actuation.close()
            peer.stop()
            audit_logger.close()

    def test_no_supervisor_means_no_veto(self, tmp_path):
        """The three-board arrangement still works, with nothing watching it."""
        db = tmp_path / "audit.db"
        audit_logger = AuditLogger(db)
        session_id = audit_logger.open_session()
        peer = MockSTM32H5(watchdog_ms=10_000.0).start()
        actuation = open(peer.device, "rb+", buffering=0)
        gf = GovernanceFilter(
            logger=audit_logger, session_id=session_id, channel=actuation
        )
        try:
            gf.process_frame([det(confidence=0.91)])
            assert rows(db)[0]["command_sent"] == 1
            assert gf.chain_head is None
        finally:
            actuation.close()
            peer.stop()
            audit_logger.close()


# ---------------------------------------------------------------------------
# Invariant 8: witness-before-act
# ---------------------------------------------------------------------------

class TestWitnessBeforeAct:
    def test_every_logged_row_is_witnessed(self, rig):
        gf, node, _, _, db = rig
        gf.process_frame([det("person", 0.95), det("tool", 0.80), det("stop", 0.40)])
        time.sleep(SETTLE_S)
        assert [ref for ref, _ in node.retained_digests] == [1, 2, 3]

    def test_witnessed_digests_match_the_database(self, rig):
        gf, node, _, _, db = rig
        for _ in range(3):
            gf.process_frame([det()])
        time.sleep(SETTLE_S)

        conn = sqlite3.connect(db)
        result = verify_database(conn, retained=node.retained_digests)
        conn.close()
        assert result.ok, result.reason

    def test_chain_head_tracks_the_database(self, rig):
        gf, _, _, _, db = rig
        for _ in range(3):
            gf.process_frame([det()])

        conn = sqlite3.connect(db)
        recomputed = AuditChain.from_rows(read_rows(conn))
        conn.close()
        assert gf.chain_head == recomputed.head

    def test_digest_precedes_the_command_frame(self, rig):
        """The witness must hold the digest before the actuator is asked."""
        gf, node, _, _, _ = rig
        gf.process_frame([det(confidence=0.91)])
        # The Alvik ACK is only recorded after _send_command returns, and
        # record() runs before it, so a retained digest here proves ordering.
        assert node.retained_digests
        assert node.retained_digests[0][0] == 1

    def test_tampering_after_the_fact_is_detected(self, rig):
        """The whole point: an edit the append-only write path would not make."""
        gf, node, _, audit_logger, db = rig
        for _ in range(3):
            gf.process_frame([det()])
        time.sleep(SETTLE_S)
        witnessed = node.retained_digests

        conn = sqlite3.connect(db)
        conn.execute("UPDATE audit_log SET confidence = 0.05 WHERE id = 2")
        conn.commit()
        result = verify_database(conn, retained=witnessed)
        conn.close()

        assert not result.ok
        assert 2 in result.mismatches

    def test_suppressed_rows_are_witnessed_too(self, rig):
        gf, node, _, _, _ = rig
        gf.process_frame([det(confidence=0.10)])
        time.sleep(SETTLE_S)
        assert len(node.retained_digests) == 1

    def test_heartbeat_counters_reach_the_node(self, rig):
        gf, node, _, _, _ = rig
        gf.process_frame([det("person", 0.95), det("tool", 0.80)])
        time.sleep(SETTLE_S)
        hb = node.last_heartbeat
        assert hb is not None
        assert hb.events_logged == 2
        assert hb.commands_sent == 1


# ---------------------------------------------------------------------------
# Interaction with the pre-existing invariants
# ---------------------------------------------------------------------------

class TestInteractionWithExistingGates:
    def test_empty_frame_does_not_poll_or_chain(self, rig):
        gf, node, link, _, db = rig
        gf.process_frame([])
        assert rows(db) == []
        assert link.chain.count == 0

    def test_one_command_per_frame_still_holds(self, rig):
        gf, _, _, _, db = rig
        gf.process_frame([det("person", 0.95), det("stop", 0.90, "gesture")])
        logged = rows(db)
        assert [r["command_sent"] for r in logged] == [1, 0]

    def test_confidence_gate_still_holds(self, rig):
        gf, _, _, _, db = rig
        gf.process_frame([det(confidence=0.30)])
        assert rows(db)[0]["command_sent"] == 0

    def test_stm32_reject_still_recorded(self, rig):
        gf, _, _, _, db = rig
        gf.process_frame([det(confidence=0.70)])  # float32 rounds below the gate
        row = rows(db)[0]
        assert row["command_sent"] == 1
        assert row["stm32_ack"] == 0
