"""
Governed Edge AI: Audit Dashboard API.

Read-only access to the audit log for the browser dashboard.
Human-reviewer flag annotation is the only write path.
No outbound telemetry; LAN-only by design (see README governance section).

Routes:
  GET  /health
  GET  /sessions
  GET  /events          ?session_id= &actor= &flagged= &limit= &offset=
  POST /events/{id}/flag
  GET  /query           ?q=<natural language>   (LLM stub: Step 5)
"""

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from dashboard.models import (
    EventOut,
    FlagRequest,
    FlagResponse,
    QueryResponse,
    SessionOut,
)
from observability import init_sentry

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_DB = str(Path(__file__).parent.parent / "audit.db")
DB_PATH: Path = Path(os.environ.get("AUDIT_DB_PATH", _DEFAULT_DB))

init_sentry("audit-dashboard")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Governed Edge AI: Audit Dashboard",
    description="Read-only view of the append-only governance audit log.",
    version="0.1.0",
)

# LAN-only: restrict origins in production via ALLOWED_ORIGINS env var
_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------

def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Open a read/write SQLite connection per request. WAL mode allows
    concurrent readers alongside the logger's write connection."""
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


Db = Annotated[sqlite3.Connection, Depends(get_db)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    return {"status": "ok", "db": str(DB_PATH)}


@app.get("/sessions", response_model=list[SessionOut], tags=["audit"])
def list_sessions(db: Db) -> list[dict[str, object]]:
    rows = db.execute(
        "SELECT session_id, started_at, ended_at, board_serial, notes"
        " FROM sessions ORDER BY started_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/events", response_model=list[EventOut], tags=["audit"])
def list_events(
    db: Db,
    session_id: str | None = Query(default=None),
    actor: str | None = Query(default=None, pattern="^(ai|human_override|oversight)$"),
    flagged: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    clauses: list[str] = []
    params: list[object] = []

    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if actor is not None:
        clauses.append("actor = ?")
        params.append(actor)
    if flagged is not None:
        clauses.append("flag = ?")
        params.append(int(flagged))

    params += [limit, offset]

    sql = (
        "SELECT id, ts, session_id, actor, detection_type, detection_label,"
        "       confidence, command, command_sent, stm32_ack, flag, notes"
        " FROM audit_log"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"

    rows = db.execute(sql, params).fetchall()

    return [_coerce_event(dict(r)) for r in rows]


@app.post("/events/{event_id}/flag", response_model=FlagResponse, tags=["audit"])
def flag_event(event_id: int, body: FlagRequest, db: Db) -> dict[str, object]:
    row = db.execute(
        "SELECT id FROM audit_log WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    db.execute(
        "UPDATE audit_log SET flag = 1, notes = COALESCE(?, notes) WHERE id = ?",
        (body.notes, event_id),
    )
    return {"event_id": event_id, "flagged": True}


@app.get("/query", response_model=QueryResponse, tags=["llm"])
def query(
    q: str = Query(..., min_length=1, description="Natural-language query over the audit log"),
) -> dict[str, str]:
    return {
        "query": q,
        "answer": (
            "LLM query interface not yet connected. "
            "See audit-service/llm_query/ (Step 5)."
        ),
        "note": "stub",
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _coerce_event(row: dict[str, object]) -> dict[str, object]:
    """Convert sqlite3 integer booleans to Python bools for the response model."""
    row["command_sent"] = bool(row["command_sent"])
    row["stm32_ack"] = None if row["stm32_ack"] is None else bool(row["stm32_ack"])
    row["flag"] = bool(row["flag"])
    return row
