"""
Tests for audit-service/logger.py.

Covers: session lifecycle, event insertion, constraint validation,
flag annotation, STM32H5 ack recording, and append-only semantics.

Marker conventions (see pyproject.toml):
  @pytest.mark.smoke      - fast sanity, run first
  @pytest.mark.regression - guards a previously found bug
  @pytest.mark.unit       - no I/O side-effects (all tests here qualify)
  @pytest.mark.integration - real disk I/O
"""

import sqlite3
from pathlib import Path

import pytest

from logger import AuditEvent, AuditLogger

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "test_audit.db"


@pytest.fixture
def logger(db_path) -> AuditLogger:
    with AuditLogger(db_path) as lg:
        yield lg


@pytest.fixture
def session_id(logger) -> str:
    return logger.open_session(board_serial="TEST-001")


def _event(**overrides) -> AuditEvent:
    defaults = {
        "session_id": "placeholder",
        "actor": "ai",
        "detection_type": "object",
        "detection_label": "person",
        "confidence": 0.85,
        "command": "HALT",
        "command_sent": True,
        "stm32_ack": None,
    }
    defaults.update(overrides)
    return AuditEvent(**defaults)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

class TestSessionLifecycle:
    def test_open_returns_uuid(self, logger):
        sid = logger.open_session()
        assert len(sid) == 36
        assert sid.count("-") == 4

    def test_open_with_board_serial(self, logger):
        sid = logger.open_session(board_serial="VENTUNO-Q-001")
        row = logger._conn.execute(
            "SELECT board_serial FROM sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        assert row[0] == "VENTUNO-Q-001"

    def test_open_multiple_sessions_are_distinct(self, logger):
        sid1 = logger.open_session()
        sid2 = logger.open_session()
        assert sid1 != sid2

    def test_close_session_sets_ended_at(self, logger):
        sid = logger.open_session()
        logger.close_session(sid)
        row = logger._conn.execute(
            "SELECT ended_at FROM sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        assert row[0] is not None

    def test_close_session_appends_notes(self, logger):
        sid = logger.open_session()
        logger.close_session(sid, notes="clean shutdown")
        row = logger._conn.execute(
            "SELECT notes FROM sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        assert row[0] == "clean shutdown"

    def test_new_session_ended_at_is_null(self, logger):
        sid = logger.open_session()
        row = logger._conn.execute(
            "SELECT ended_at FROM sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        assert row[0] is None


# ---------------------------------------------------------------------------
# Event insertion
# ---------------------------------------------------------------------------

class TestLogEvent:
    def test_returns_integer_id(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id))
        assert isinstance(eid, int)
        assert eid == 1

    def test_ids_increment(self, logger, session_id):
        e = _event(session_id=session_id)
        ids = [logger.log_event(e) for _ in range(5)]
        assert ids == list(range(1, 6))

    def test_all_columns_persisted(self, logger, session_id):
        eid = logger.log_event(_event(
            session_id=session_id,
            actor="human_override",
            detection_type="gesture",
            detection_label="thumbs_up",
            confidence=0.72,
            command="NONE",
            command_sent=False,
            stm32_ack=None,
            flag=False,
            notes="manual review",
        ))
        row = logger._conn.execute(
            "SELECT actor, detection_type, detection_label, confidence,"
            "       command, command_sent, stm32_ack, flag, notes"
            " FROM audit_log WHERE id = ?",
            (eid,),
        ).fetchone()
        assert row == (
            "human_override", "gesture", "thumbs_up",
            0.72, "NONE", 0, None, 0, "manual review",
        )

    def test_stm32_ack_true_stored_as_1(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id, stm32_ack=True))
        val = logger._conn.execute(
            "SELECT stm32_ack FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert val == 1

    def test_stm32_ack_false_stored_as_0(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id, stm32_ack=False))
        val = logger._conn.execute(
            "SELECT stm32_ack FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert val == 0

    def test_stm32_ack_none_stored_as_null(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id, stm32_ack=None))
        val = logger._conn.execute(
            "SELECT stm32_ack FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert val is None

    def test_flag_true_stored_as_1(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id, flag=True))
        val = logger._conn.execute(
            "SELECT flag FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert val == 1

    def test_ts_is_iso8601_utc(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id))
        ts = logger._conn.execute(
            "SELECT ts FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert ts.endswith("+00:00")
        assert "T" in ts

    def test_command_sent_false_stored_as_0(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id, command_sent=False))
        val = logger._conn.execute(
            "SELECT command_sent FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert val == 0


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_invalid_actor_raises(self, logger, session_id):
        with pytest.raises(ValueError, match="actor"):
            logger.log_event(_event(session_id=session_id, actor="robot"))

    def test_invalid_detection_type_raises(self, logger, session_id):
        with pytest.raises(ValueError, match="detection_type"):
            logger.log_event(_event(session_id=session_id, detection_type="audio"))

    def test_confidence_above_1_raises(self, logger, session_id):
        with pytest.raises(ValueError, match="confidence"):
            logger.log_event(_event(session_id=session_id, confidence=1.01))

    def test_confidence_below_0_raises(self, logger, session_id):
        with pytest.raises(ValueError, match="confidence"):
            logger.log_event(_event(session_id=session_id, confidence=-0.01))

    def test_confidence_boundaries_are_valid(self, logger, session_id):
        logger.log_event(_event(session_id=session_id, confidence=0.0))
        logger.log_event(_event(session_id=session_id, confidence=1.0))

    def test_all_valid_actors_accepted(self, logger, session_id):
        for actor in ("ai", "human_override"):
            logger.log_event(_event(session_id=session_id, actor=actor))

    def test_all_valid_detection_types_accepted(self, logger, session_id):
        for dt in ("object", "gesture", "pose"):
            logger.log_event(_event(session_id=session_id, detection_type=dt))


# ---------------------------------------------------------------------------
# STM32H5 ACK recording
# ---------------------------------------------------------------------------

class TestUpdateStm32Ack:
    def test_sets_ack_to_1(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id))
        logger.update_stm32_ack(eid, ack=True)
        val = logger._conn.execute(
            "SELECT stm32_ack FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert val == 1

    def test_sets_ack_to_0(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id))
        logger.update_stm32_ack(eid, ack=False)
        val = logger._conn.execute(
            "SELECT stm32_ack FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert val == 0

    @pytest.mark.regression
    def test_does_not_overwrite_existing_ack(self, logger, session_id):
        # Guard: a late-arriving duplicate ACK must not flip a confirmed record.
        eid = logger.log_event(_event(session_id=session_id, stm32_ack=True))
        logger.update_stm32_ack(eid, ack=False)
        val = logger._conn.execute(
            "SELECT stm32_ack FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert val == 1


# ---------------------------------------------------------------------------
# Flag annotation
# ---------------------------------------------------------------------------

class TestFlagEvent:
    def test_sets_flag_to_1(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id))
        logger.flag_event(eid)
        val = logger._conn.execute(
            "SELECT flag FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert val == 1

    def test_flag_with_notes(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id))
        logger.flag_event(eid, notes="confidence drift detected")
        row = logger._conn.execute(
            "SELECT flag, notes FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()
        assert row == (1, "confidence drift detected")

    def test_flag_preserves_existing_notes_when_none_passed(self, logger, session_id):
        eid = logger.log_event(_event(session_id=session_id, notes="original note"))
        logger.flag_event(eid, notes=None)
        notes = logger._conn.execute(
            "SELECT notes FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()[0]
        assert notes == "original note"


# ---------------------------------------------------------------------------
# Append-only / durability
# ---------------------------------------------------------------------------

class TestAppendOnly:
    def test_wal_mode_is_active(self, logger):
        mode = logger._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    @pytest.mark.integration
    def test_rows_survive_reconnect(self, db_path):
        with AuditLogger(db_path) as lg:
            sid = lg.open_session()
            eid = lg.log_event(_event(session_id=sid))

        with AuditLogger(db_path) as lg2:
            row = lg2._conn.execute(
                "SELECT id FROM audit_log WHERE id = ?", (eid,)
            ).fetchone()
            assert row is not None
            assert row[0] == eid

    def test_context_manager_closes_connection(self, db_path):
        with AuditLogger(db_path) as lg:
            pass
        with pytest.raises(sqlite3.ProgrammingError):
            lg._conn.execute("SELECT 1")

    def test_partial_index_on_flag(self, logger):
        indexes = [
            row[1]
            for row in logger._conn.execute(
                "SELECT * FROM sqlite_master WHERE type='index'"
            ).fetchall()
        ]
        assert "idx_audit_flag" in indexes
