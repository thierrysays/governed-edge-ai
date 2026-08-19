"""
Tests for the dashboard's real database dependency.

Every other dashboard test overrides get_db with a temp connection, which is
the right way to test the routes but leaves the production connection path
itself unexercised. These tests drive it directly.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from dashboard import app as app_module
from logger import AuditEvent, AuditLogger


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    """Point the dashboard at a real database with one row in it."""
    db = tmp_path / "audit.db"
    logger = AuditLogger(db)
    session_id = logger.open_session(board_serial="R4-DASH")
    logger.log_event(AuditEvent(
        session_id=session_id, actor="oversight", detection_type="oversight",
        detection_label="operator_override", confidence=1.0,
        command="HALT", command_sent=False,
    ))
    logger.close()
    monkeypatch.setattr(app_module, "DB_PATH", db)
    return db


class TestGetDb:
    def test_yields_a_working_connection(self, live_db):
        gen = app_module.get_db()
        conn = next(gen)
        try:
            assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
        finally:
            gen.close()

    def test_rows_come_back_as_mappings(self, live_db):
        gen = app_module.get_db()
        conn = next(gen)
        try:
            row = conn.execute("SELECT actor FROM audit_log").fetchone()
            assert row["actor"] == "oversight"
        finally:
            gen.close()

    def test_connection_is_closed_on_teardown(self, live_db):
        gen = app_module.get_db()
        conn = next(gen)
        gen.close()
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_wal_mode_is_enabled(self, live_db):
        gen = app_module.get_db()
        conn = next(gen)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            gen.close()


class TestRoutesAgainstTheRealDependency:
    def test_events_route_serves_an_oversight_row(self, live_db):
        with TestClient(app_module.app) as client:
            response = client.get("/events")
        assert response.status_code == 200
        assert response.json()[0]["actor"] == "oversight"

    def test_events_can_be_filtered_by_the_oversight_actor(self, live_db):
        with TestClient(app_module.app) as client:
            matching = client.get("/events", params={"actor": "oversight"})
            other = client.get("/events", params={"actor": "ai"})
        assert len(matching.json()) == 1
        assert other.json() == []

    def test_unknown_actor_is_refused_by_the_api(self, live_db):
        with TestClient(app_module.app) as client:
            response = client.get("/events", params={"actor": "root"})
        assert response.status_code == 422

    def test_sessions_route_serves_the_session(self, live_db):
        with TestClient(app_module.app) as client:
            response = client.get("/sessions")
        assert response.json()[0]["board_serial"] == "R4-DASH"
