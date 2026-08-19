"""
Adversarial tests for the oversight tier.

The governance claim is that authority is separated in circuitry rather than
asserted in a document. That claim is only worth making if someone has tried
to break it. These tests attack the oversight link and the audit path from the
positions an attacker would actually hold, and they assert both what holds and
what does not: a control whose limits are undocumented is a control nobody can
rely on.

Threat positions covered
  T1  a compromised governance host, trying to silence its own supervisor
  T2  an attacker on the serial link between the VENTUNO Q and the R4
  T3  a compromised host with write access to the audit database
  T4  malformed or hostile input on either link
"""

import os
import sqlite3
import time

import pytest
from logger import AuditEvent, AuditLogger

from governance.filter import GovernanceFilter
from ipc.codec import (
    MAGIC,
    AttestDigest,
    OverrideAssert,
    OverrideClear,
    OverrideReason,
    SupervisorHeartbeat,
    SystemState,
    crc16_ccitt,
    encode,
)
from ipc.mock_peer import MockSTM32H5
from oversight.attestation import AuditChain, AuditRow, verify_database
from oversight.mock_supervisor import MockR4Supervisor
from oversight.supervisor_link import SupervisorLink
from perception.base import DetectionResult

SETTLE_S = 0.15


def _row(ref: int) -> AuditRow:
    return AuditRow(
        audit_ref=ref, ts="2026-08-19T10:00:00.000000+00:00", session_id="s",
        actor="ai", detection_type="object", detection_label="person",
        confidence=0.91, command="HALT", command_sent=True,
    )


@pytest.fixture
def node():
    with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as n:
        yield n


@pytest.fixture
def channel(node):
    ch = open(node.device, "rb+", buffering=0)
    yield ch
    ch.close()


# ---------------------------------------------------------------------------
# T1: a compromised governance host
# ---------------------------------------------------------------------------

class TestGovernanceHostCannotSilenceItsSupervisor:
    def test_no_message_type_clears_an_override(self, node, channel):
        """The protocol has no OVERRIDE_DENY, and nothing the governance tier
        can send acts as one. Throw the whole outbound vocabulary at it."""
        node.press_button()
        node.release_button()
        assert node.override_active

        for frame in (
            encode(SupervisorHeartbeat(1, SystemState.ARMED, 1, 0)),
            encode(AttestDigest(1, b"\x01" * 32)),
            encode(OverrideClear(timestamp_us=1)),      # the R4's own message,
            encode(OverrideAssert(1, OverrideReason.REMOTE_CONSOLE)),  # replayed
        ):
            channel.write(frame)
        time.sleep(0.3)

        assert node.override_active is True
        assert node.kill_line_asserted is True

    def test_flooding_heartbeats_does_not_clear_an_override(self, node, channel):
        node.press_button()
        node.release_button()
        for _ in range(200):
            channel.write(encode(SupervisorHeartbeat(1, SystemState.ARMED, 1, 0)))
        time.sleep(0.3)
        assert node.override_active is True

    def test_reporting_a_healthy_state_does_not_help(self, node, channel):
        """A compromised host claiming ARMED is still just a claim."""
        node.press_button()
        channel.write(encode(SupervisorHeartbeat(999, SystemState.ARMED, 999, 999)))
        time.sleep(SETTLE_S)
        assert node.override_active is True


# ---------------------------------------------------------------------------
# T2: an attacker on the oversight serial link
# ---------------------------------------------------------------------------

class TestSerialLinkTrustBoundary:
    def test_a_forged_clear_releases_the_soft_veto(self):
        """Stated plainly because it is a real limit: the link's trust
        boundary is the physical USB-C cable. Anyone who can write to it can
        forge an OVERRIDE_CLEAR and release the governance filter's veto."""
        link_read, attacker = os.pipe()
        link = SupervisorLink(open(link_read, "rb", buffering=0), fail_closed=False)
        try:
            os.write(attacker, encode(OverrideAssert(
                1, OverrideReason.OPERATOR_BUTTON,
            )))
            assert link.poll() is True
            os.write(attacker, encode(OverrideClear(timestamp_us=2)))
            assert link.poll() is False
        finally:
            link.close()
            os.close(attacker)

    def test_the_hard_kill_line_is_not_on_the_wire(self, node, channel):
        """Which is why there are two paths. The forged clear above reaches
        only the soft veto. The GPIO line into the Alvik kill-switch input is
        driven from the node's own latch, and no frame can reach it."""
        node.press_button()
        node.release_button()
        channel.write(encode(OverrideClear(timestamp_us=1)))
        time.sleep(SETTLE_S)
        assert node.kill_line_asserted is True

    def test_the_alvik_still_refuses_while_the_line_is_held(self, tmp_path):
        """End to end: with the kill line asserted, the actuation MCU rejects
        the command even if the governance tier transmits it anyway."""
        db = tmp_path / "audit.db"
        audit_logger = AuditLogger(db)
        session_id = audit_logger.open_session()
        peer = MockSTM32H5(watchdog_ms=10_000.0).start()
        actuation = open(peer.device, "rb+", buffering=0)
        gf = GovernanceFilter(
            logger=audit_logger, session_id=session_id, channel=actuation,
        )
        try:
            peer.trigger_kill_switch()   # the R4's GPIO line, from the MCU's side
            time.sleep(0.05)
            gf.process_frame([DetectionResult("object", "person", 0.99)])
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM audit_log").fetchone()
            conn.close()
            assert row["command_sent"] == 1   # the host transmitted
            assert row["stm32_ack"] == 0      # the hardware refused
        finally:
            actuation.close()
            peer.stop()
            audit_logger.close()


# ---------------------------------------------------------------------------
# T3: a compromised host with write access to the audit database
# ---------------------------------------------------------------------------

class TestAuditTampering:
    @pytest.fixture
    def witnessed(self, tmp_path):
        """Five rows written and witnessed by an oversight node."""
        db = tmp_path / "audit.db"
        logger = AuditLogger(db)
        session_id = logger.open_session()
        chain = AuditChain()
        digests = []
        for i in range(5):
            ref = logger.log_event(AuditEvent(
                session_id=session_id, actor="ai", detection_type="object",
                detection_label="person", confidence=0.80 + i / 100,
                command="HALT", command_sent=(i == 0),
            ))
            digests.append((ref, chain.append(AuditRow.from_mapping(
                logger.fetch_event(ref)
            ))))
        logger.close()
        return db, digests

    def _verify(self, db, digests):
        conn = sqlite3.connect(db)
        result = verify_database(conn, retained=digests)
        conn.close()
        return result

    def test_baseline_verifies(self, witnessed):
        db, digests = witnessed
        assert self._verify(db, digests).ok

    def test_editing_a_confidence_is_detected(self, witnessed):
        db, digests = witnessed
        conn = sqlite3.connect(db)
        conn.execute("UPDATE audit_log SET confidence = 0.01 WHERE id = 3")
        conn.commit()
        conn.close()
        assert not self._verify(db, digests).ok

    def test_editing_a_label_is_detected(self, witnessed):
        db, digests = witnessed
        conn = sqlite3.connect(db)
        conn.execute("UPDATE audit_log SET detection_label = 'nothing' WHERE id = 1")
        conn.commit()
        conn.close()
        assert not self._verify(db, digests).ok

    def test_rewriting_command_sent_is_detected(self, witnessed):
        """The edit an operator would most want to make after an incident."""
        db, digests = witnessed
        conn = sqlite3.connect(db)
        conn.execute("UPDATE audit_log SET command_sent = 0 WHERE id = 1")
        conn.commit()
        conn.close()
        assert not self._verify(db, digests).ok

    def test_backdating_a_timestamp_is_detected(self, witnessed):
        db, digests = witnessed
        conn = sqlite3.connect(db)
        conn.execute("UPDATE audit_log SET ts = '1999-01-01T00:00:00' WHERE id = 4")
        conn.commit()
        conn.close()
        assert not self._verify(db, digests).ok

    def test_deleting_a_row_is_detected(self, witnessed):
        db, digests = witnessed
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM audit_log WHERE id = 3")
        conn.commit()
        conn.close()
        result = self._verify(db, digests)
        assert not result.ok
        assert result.first_missing_ref == 3

    def test_deleting_the_whole_log_is_detected(self, witnessed):
        db, digests = witnessed
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM audit_log")
        conn.commit()
        conn.close()
        assert not self._verify(db, digests).ok

    def test_reinserting_a_deleted_row_verbatim_still_fails(self, witnessed):
        """AUTOINCREMENT does not reuse an id, so the row comes back with a
        new one and the gap remains."""
        db, digests = witnessed
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT ts, session_id, actor, detection_type, detection_label,"
            " confidence, command, command_sent FROM audit_log WHERE id = 3"
        ).fetchone()
        conn.execute("DELETE FROM audit_log WHERE id = 3")
        conn.execute(
            "INSERT INTO audit_log (ts, session_id, actor, detection_type,"
            " detection_label, confidence, command, command_sent)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row,
        )
        conn.commit()
        conn.close()
        assert not self._verify(db, digests).ok

    def test_an_unwitnessed_database_cannot_be_vouched_for(self, witnessed):
        """Recomputation alone proves internal consistency, not authenticity.
        Without the retained digests a rewritten log verifies clean, which is
        exactly why the digests live on another board."""
        db, _ = witnessed
        conn = sqlite3.connect(db)
        conn.execute("UPDATE audit_log SET confidence = 0.01 WHERE id = 3")
        conn.commit()
        result = verify_database(conn)   # no witness supplied
        conn.close()
        assert result.ok


# ---------------------------------------------------------------------------
# T4: malformed and hostile input
# ---------------------------------------------------------------------------

class TestHostileInput:
    def test_oversized_length_header_does_not_hang_the_node(self, node, channel):
        """A header claiming 0xFFFF bytes must not wedge the reader."""
        body = bytes([MAGIC, 0x31, 0xFF, 0xFF])
        channel.write(body + crc16_ccitt(body).to_bytes(2, "little"))
        time.sleep(SETTLE_S)
        channel.write(encode(SupervisorHeartbeat(1, SystemState.ARMED, 1, 0)))
        time.sleep(0.3)
        assert node.stats.heartbeats_received == 1

    def test_random_bytes_do_not_move_the_state_machine(self, node, channel):
        channel.write(bytes(range(256)) * 4)
        time.sleep(0.3)
        assert node.override_active is False
        assert node.stats.digests_received == 0

    def test_corrupt_override_frame_is_ignored_by_the_link(self):
        link_read, attacker = os.pipe()
        link = SupervisorLink(open(link_read, "rb", buffering=0), fail_closed=False)
        try:
            corrupt = bytearray(encode(OverrideAssert(
                1, OverrideReason.OPERATOR_BUTTON,
            )))
            corrupt[-1] ^= 0xFF   # break the CRC
            os.write(attacker, bytes(corrupt))
            assert link.poll() is False
        finally:
            link.close()
            os.close(attacker)

    def test_a_hostile_detection_label_cannot_reach_sql(self, tmp_path):
        """Labels come from a model, which an attacker may influence. The
        write path is parameterised; prove it."""
        db = tmp_path / "audit.db"
        logger = AuditLogger(db)
        session_id = logger.open_session()
        hostile = "person'); DROP TABLE audit_log; --"
        ref = logger.log_event(AuditEvent(
            session_id=session_id, actor="ai", detection_type="object",
            detection_label=hostile, confidence=0.9,
            command="HALT", command_sent=False,
        ))
        assert logger.fetch_event(ref)["detection_label"] == hostile
        logger.close()

        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
        conn.close()

    def test_a_hostile_label_survives_the_chain_unchanged(self, tmp_path):
        db = tmp_path / "audit.db"
        logger = AuditLogger(db)
        session_id = logger.open_session()
        for label in ("a\x1fb", "a\nb", "a\\b", "\x00"):
            logger.log_event(AuditEvent(
                session_id=session_id, actor="ai", detection_type="object",
                detection_label=label, confidence=0.9,
                command="HALT", command_sent=False,
            ))
        logger.close()

        conn = sqlite3.connect(db)
        result = verify_database(conn)
        conn.close()
        assert result.ok
        assert result.row_count == 4

    def test_separator_injection_cannot_forge_a_matching_digest(self):
        """A label containing the field separator must not let one row hash
        the same as a different row."""
        honest = AuditRow(**{**vars(_row(1)), "detection_label": "person"})
        forged = AuditRow(**{
            **vars(_row(1)),
            "detection_label": "person\x1fHALT\x1f1",
            "command": "",
            "command_sent": False,
        })
        assert honest.canonical() != forged.canonical()
