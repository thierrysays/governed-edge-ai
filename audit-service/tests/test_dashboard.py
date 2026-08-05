"""
Tests for audit-service/dashboard/app.py.

Uses FastAPI TestClient with a dependency override that points get_db at
a temp SQLite DB pre-populated by AuditLogger — the same write path used
in production.
"""

import sqlite3
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from logger import AuditEvent, AuditLogger
from dashboard.app import app, get_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "test_audit.db"


@pytest.fixture
def seeded(db_path):
    """Populate the DB with one session and three events via AuditLogger."""
    with AuditLogger(db_path) as lg:
        sid = lg.open_session(board_serial="TEST-BOARD-001")

        eid_ai = lg.log_event(AuditEvent(
            session_id=sid, actor="ai",
            detection_type="object", detection_label="person",
            confidence=0.91, command="HALT",
            command_sent=True, stm32_ack=True,
        ))
        eid_human = lg.log_event(AuditEvent(
            session_id=sid, actor="human_override",
            detection_type="gesture", detection_label="stop",
            confidence=1.0, command="HALT",
            command_sent=True, stm32_ack=True,
            notes="operator intervened",
        ))
        eid_flagged = lg.log_event(AuditEvent(
            session_id=sid, actor="ai",
            detection_type="pose", detection_label="proximity_breach",
            confidence=0.58, command="HALT",
            command_sent=True, flag=True,
        ))

    return {"session_id": sid, "eid_ai": eid_ai, "eid_human": eid_human,
            "eid_flagged": eid_flagged, "db_path": db_path}


@pytest.fixture
def client(db_path, seeded):
    """TestClient with get_db overridden to use the temp DB."""
    def override() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c, seeded
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_200(self, client):
        c, _ = client
        r = c.get("/health")
        assert r.status_code == 200

    def test_status_ok(self, client):
        c, _ = client
        assert r.json()["status"] == "ok" if (r := c.get("/health")) else False

    def test_returns_status_ok(self, client):
        c, _ = client
        assert c.get("/health").json()["status"] == "ok"


# ---------------------------------------------------------------------------
# /sessions
# ---------------------------------------------------------------------------

class TestSessions:
    def test_returns_200(self, client):
        c, _ = client
        assert c.get("/sessions").status_code == 200

    def test_returns_list(self, client):
        c, _ = client
        body = c.get("/sessions").json()
        assert isinstance(body, list)

    def test_one_session_present(self, client):
        c, seed = client
        sessions = c.get("/sessions").json()
        assert len(sessions) == 1

    def test_session_id_matches(self, client):
        c, seed = client
        sessions = c.get("/sessions").json()
        assert sessions[0]["session_id"] == seed["session_id"]

    def test_board_serial_present(self, client):
        c, _ = client
        sessions = c.get("/sessions").json()
        assert sessions[0]["board_serial"] == "TEST-BOARD-001"

    def test_ended_at_is_null(self, client):
        c, _ = client
        # Session not closed in seeded fixture — ended_at should be None
        assert c.get("/sessions").json()[0]["ended_at"] is None


# ---------------------------------------------------------------------------
# /events
# ---------------------------------------------------------------------------

class TestEvents:
    def test_returns_200(self, client):
        c, _ = client
        assert c.get("/events").status_code == 200

    def test_returns_all_three_events(self, client):
        c, _ = client
        assert len(c.get("/events").json()) == 3

    def test_filter_by_actor_ai(self, client):
        c, _ = client
        events = c.get("/events", params={"actor": "ai"}).json()
        assert len(events) == 2
        assert all(e["actor"] == "ai" for e in events)

    def test_filter_by_actor_human_override(self, client):
        c, _ = client
        events = c.get("/events", params={"actor": "human_override"}).json()
        assert len(events) == 1
        assert events[0]["actor"] == "human_override"

    def test_filter_by_session_id(self, client):
        c, seed = client
        events = c.get("/events", params={"session_id": seed["session_id"]}).json()
        assert len(events) == 3

    def test_filter_by_session_id_unknown_returns_empty(self, client):
        c, _ = client
        events = c.get("/events", params={"session_id": "does-not-exist"}).json()
        assert events == []

    def test_filter_flagged_true(self, client):
        c, seed = client
        events = c.get("/events", params={"flagged": "true"}).json()
        assert len(events) == 1
        assert events[0]["id"] == seed["eid_flagged"]

    def test_filter_flagged_false(self, client):
        c, _ = client
        events = c.get("/events", params={"flagged": "false"}).json()
        assert len(events) == 2

    def test_limit_parameter(self, client):
        c, _ = client
        events = c.get("/events", params={"limit": 1}).json()
        assert len(events) == 1

    def test_offset_parameter(self, client):
        c, _ = client
        all_events = c.get("/events").json()
        offset_events = c.get("/events", params={"offset": 1}).json()
        assert len(offset_events) == 2
        assert offset_events[0]["id"] == all_events[1]["id"]

    def test_command_sent_is_bool(self, client):
        c, _ = client
        for event in c.get("/events").json():
            assert isinstance(event["command_sent"], bool)

    def test_flag_is_bool(self, client):
        c, _ = client
        for event in c.get("/events").json():
            assert isinstance(event["flag"], bool)

    def test_stm32_ack_is_bool_or_none(self, client):
        c, _ = client
        for event in c.get("/events").json():
            assert event["stm32_ack"] in (True, False, None)

    def test_events_ordered_by_ts_desc(self, client):
        c, _ = client
        events = c.get("/events").json()
        timestamps = [e["ts"] for e in events]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_invalid_actor_returns_422(self, client):
        c, _ = client
        assert c.get("/events", params={"actor": "robot"}).status_code == 422

    def test_combined_filters(self, client):
        c, seed = client
        events = c.get("/events", params={
            "actor": "ai",
            "session_id": seed["session_id"],
        }).json()
        assert all(e["actor"] == "ai" for e in events)

    def test_notes_preserved(self, client):
        c, _ = client
        events = c.get("/events", params={"actor": "human_override"}).json()
        assert events[0]["notes"] == "operator intervened"


# ---------------------------------------------------------------------------
# /events/{id}/flag
# ---------------------------------------------------------------------------

class TestFlagEvent:
    def test_flag_unflagged_event(self, client):
        c, seed = client
        r = c.post(f"/events/{seed['eid_ai']}/flag", json={})
        assert r.status_code == 200
        assert r.json()["flagged"] is True

    def test_flag_with_notes(self, client):
        c, seed = client
        c.post(f"/events/{seed['eid_ai']}/flag",
               json={"notes": "reviewer annotation"})
        events = c.get("/events", params={"flagged": "true"}).json()
        ids = [e["id"] for e in events]
        assert seed["eid_ai"] in ids

    def test_flag_already_flagged_event_is_idempotent(self, client):
        c, seed = client
        r1 = c.post(f"/events/{seed['eid_flagged']}/flag", json={})
        r2 = c.post(f"/events/{seed['eid_flagged']}/flag", json={})
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_flag_nonexistent_event_returns_404(self, client):
        c, _ = client
        assert c.post("/events/99999/flag", json={}).status_code == 404

    def test_response_contains_event_id(self, client):
        c, seed = client
        r = c.post(f"/events/{seed['eid_ai']}/flag", json={})
        assert r.json()["event_id"] == seed["eid_ai"]


# ---------------------------------------------------------------------------
# /query (stub)
# ---------------------------------------------------------------------------

class TestQuery:
    def test_returns_200(self, client):
        c, _ = client
        assert c.get("/query", params={"q": "how many halts in the last hour"}).status_code == 200

    def test_echoes_query(self, client):
        c, _ = client
        q = "show all suppressed commands"
        r = c.get("/query", params={"q": q}).json()
        assert r["query"] == q

    def test_note_is_stub(self, client):
        c, _ = client
        r = c.get("/query", params={"q": "test"}).json()
        assert r["note"] == "stub"

    def test_missing_q_returns_422(self, client):
        c, _ = client
        assert c.get("/query").status_code == 422

    def test_empty_q_returns_422(self, client):
        c, _ = client
        assert c.get("/query", params={"q": ""}).status_code == 422
