"""
Tests for the audit-log changes the UNO R4 WiFi oversight node introduced:

  - the 'oversight' actor and detection_type, for machine-initiated action by
    the supervisor (heartbeat lost, attestation mismatch)
  - fetch_event(), the read-back the oversight tier hashes into the
    attestation chain
"""

import sqlite3

import pytest

from logger import AuditEvent, AuditLogger


@pytest.fixture
def logger(tmp_path):
    log = AuditLogger(tmp_path / "audit.db")
    yield log
    log.close()


@pytest.fixture
def session(logger):
    return logger.open_session(board_serial="R4-OVERSIGHT")


def _event(session_id: str, **overrides) -> AuditEvent:
    fields = {
        "session_id": session_id,
        "actor": "ai",
        "detection_type": "object",
        "detection_label": "person",
        "confidence": 0.91,
        "command": "HALT",
        "command_sent": True,
    }
    fields.update(overrides)
    return AuditEvent(**fields)


# ---------------------------------------------------------------------------
# The oversight actor
# ---------------------------------------------------------------------------

class TestOversightActor:
    def test_oversight_actor_accepted(self, logger, session):
        ref = logger.log_event(_event(
            session, actor="oversight", detection_type="oversight",
            detection_label="governance_heartbeat_lost", confidence=1.0,
            command="HALT", command_sent=False,
        ))
        assert ref >= 1

    def test_human_override_still_accepted(self, logger, session):
        assert logger.log_event(_event(session, actor="human_override")) >= 1

    def test_ai_still_accepted(self, logger, session):
        assert logger.log_event(_event(session, actor="ai")) >= 1

    def test_unknown_actor_still_rejected(self, logger, session):
        with pytest.raises(ValueError, match="actor must be one of"):
            logger.log_event(_event(session, actor="root"))

    def test_error_message_lists_oversight(self, logger, session):
        with pytest.raises(ValueError, match="oversight"):
            logger.log_event(_event(session, actor="nobody"))

    def test_oversight_detection_type_accepted(self, logger, session):
        assert logger.log_event(_event(session, detection_type="oversight")) >= 1

    def test_unknown_detection_type_still_rejected(self, logger, session):
        with pytest.raises(ValueError, match="detection_type must be one of"):
            logger.log_event(_event(session, detection_type="telemetry"))

    def test_schema_check_constraint_allows_oversight(self, tmp_path):
        """The CHECK in schema.sql must agree with the Python validator."""
        db = tmp_path / "audit.db"
        log = AuditLogger(db)
        session_id = log.open_session()
        log.close()

        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO audit_log (ts, session_id, actor, detection_type,"
            " detection_label, confidence, command, command_sent)"
            " VALUES ('t', ?, 'oversight', 'oversight', 'attestation_mismatch',"
            " 1.0, 'HALT', 0)",
            (session_id,),
        )
        conn.commit()
        assert conn.execute(
            "SELECT actor FROM audit_log"
        ).fetchone()[0] == "oversight"
        conn.close()

    def test_schema_check_still_rejects_an_unknown_actor(self, tmp_path):
        db = tmp_path / "audit.db"
        log = AuditLogger(db)
        session_id = log.open_session()
        log.close()

        conn = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audit_log (ts, session_id, actor, detection_type,"
                " detection_label, confidence, command, command_sent)"
                " VALUES ('t', ?, 'root', 'object', 'person', 1.0, 'HALT', 0)",
                (session_id,),
            )
        conn.close()

    def test_oversight_rows_are_queryable_by_actor(self, logger, session):
        logger.log_event(_event(session, actor="ai"))
        logger.log_event(_event(
            session, actor="oversight", detection_type="oversight",
            detection_label="operator_override", command_sent=False,
        ))
        rows = logger._conn.execute(  # noqa: SLF001
            "SELECT detection_label FROM audit_log WHERE actor = 'oversight'"
        ).fetchall()
        assert [r[0] for r in rows] == ["operator_override"]


# ---------------------------------------------------------------------------
# fetch_event
# ---------------------------------------------------------------------------

class TestFetchEvent:
    def test_returns_the_row_just_written(self, logger, session):
        ref = logger.log_event(_event(session, detection_label="tool"))
        row = logger.fetch_event(ref)
        assert row is not None
        assert row["id"] == ref
        assert row["detection_label"] == "tool"

    def test_returns_none_for_a_missing_id(self, logger, session):
        logger.log_event(_event(session))
        assert logger.fetch_event(9999) is None

    def test_row_supports_keys(self, logger, session):
        """AuditRow.from_mapping() relies on sqlite3.Row.keys()."""
        ref = logger.log_event(_event(session))
        assert "detection_type" in logger.fetch_event(ref).keys()  # noqa: SIM118

    def test_exposes_only_the_chained_columns(self, logger, session):
        """stm32_ack and flag are written after the row and must stay out of
        the chain, so fetch_event does not offer them."""
        ref = logger.log_event(_event(session))
        columns = set(logger.fetch_event(ref).keys())
        assert columns == {
            "id", "ts", "session_id", "actor", "detection_type",
            "detection_label", "confidence", "command", "command_sent",
        }

    def test_confidence_round_trips_as_a_float(self, logger, session):
        ref = logger.log_event(_event(session, confidence=0.123456789012345))
        assert logger.fetch_event(ref)["confidence"] == 0.123456789012345

    def test_command_sent_is_stored_as_an_integer(self, logger, session):
        sent = logger.fetch_event(logger.log_event(_event(session, command_sent=True)))
        held = logger.fetch_event(logger.log_event(_event(session, command_sent=False)))
        assert sent["command_sent"] == 1
        assert held["command_sent"] == 0

    def test_is_read_only(self, logger, session):
        """Reading a row must not disturb the columns written later."""
        ref = logger.log_event(_event(session))
        logger.update_stm32_ack(ref, True)
        logger.fetch_event(ref)
        after = logger._conn.execute(  # noqa: SLF001
            "SELECT stm32_ack FROM audit_log WHERE id = ?", (ref,)
        ).fetchone()[0]
        assert after == 1

    def test_row_count_is_unchanged_by_a_fetch(self, logger, session):
        logger.log_event(_event(session))
        before = logger._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM audit_log"
        ).fetchone()[0]
        logger.fetch_event(1)
        logger.fetch_event(1)
        after = logger._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM audit_log"
        ).fetchone()[0]
        assert before == after == 1

    def test_repeated_fetches_are_identical(self, logger, session):
        ref = logger.log_event(_event(session))
        assert tuple(logger.fetch_event(ref)) == tuple(logger.fetch_event(ref))
