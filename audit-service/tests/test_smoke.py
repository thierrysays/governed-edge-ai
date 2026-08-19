"""
Smoke tests: full end-to-end path through the audit logger.
Run these first in CI (pytest -m smoke) to catch import or schema failures
before spending time on the full suite.
"""


import pytest

from logger import AuditEvent, AuditLogger


@pytest.mark.smoke
def test_import():
    from logger import AuditEvent, AuditLogger, _now, _validate  # noqa: F401


@pytest.mark.smoke
def test_schema_applied_on_connect(tmp_path):
    with AuditLogger(tmp_path / "smoke.db") as lg:
        tables = {
            row[0]
            for row in lg._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"audit_log", "sessions"} <= tables


@pytest.mark.smoke
def test_full_command_lifecycle(tmp_path):
    """Mirrors the real usage pattern: open session → log → dispatch → ack → close."""
    with AuditLogger(tmp_path / "smoke.db") as lg:
        sid = lg.open_session(board_serial="SMOKE-001")

        # Step 1: log before act (protocol requirement)
        event_id = lg.log_event(AuditEvent(
            session_id=sid,
            actor="ai",
            detection_type="object",
            detection_label="person",
            confidence=0.91,
            command="HALT",
            command_sent=True,
        ))
        assert event_id >= 1

        # Step 2: embed event_id in COMMAND_REQUEST frame (simulated here)
        audit_ref = event_id
        assert audit_ref != 0  # protocol rejects audit_ref == 0

        # Step 3: receive STM32H5 ACK
        lg.update_stm32_ack(event_id, ack=True)

        # Step 4: verify record is complete
        row = lg._conn.execute(
            "SELECT actor, command, command_sent, stm32_ack, flag"
            " FROM audit_log WHERE id = ?",
            (event_id,),
        ).fetchone()
        assert row == ("ai", "HALT", 1, 1, 0)

        lg.close_session(sid, notes="smoke test complete")

    # Step 5: durability: re-open and verify
    with AuditLogger(tmp_path / "smoke.db") as lg2:
        count = lg2._conn.execute(
            "SELECT COUNT(*) FROM audit_log"
        ).fetchone()[0]
        assert count == 1


@pytest.mark.smoke
def test_human_override_lifecycle(tmp_path):
    """Human operator takes over: actor=human_override, notes required."""
    with AuditLogger(tmp_path / "override.db") as lg:
        sid = lg.open_session()
        eid = lg.log_event(AuditEvent(
            session_id=sid,
            actor="human_override",
            detection_type="gesture",
            detection_label="stop",
            confidence=1.0,
            command="HALT",
            command_sent=True,
            notes="operator intervened: proximity alert",
        ))
        row = lg._conn.execute(
            "SELECT actor, notes FROM audit_log WHERE id = ?", (eid,)
        ).fetchone()
        assert row[0] == "human_override"
        assert "operator intervened" in row[1]
