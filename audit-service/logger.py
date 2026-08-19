"""
Append-only audit log writer for the Governed Edge AI demonstrator.

Governance contract:
  - log_event() must be called and the returned ID confirmed before
    constructing a COMMAND_REQUEST IPC frame (audit_ref field).
  - Rows in audit_log are never deleted.
  - flag is one-way: 0 → 1 only; never reset to 0.
  - stm32_ack starts NULL (no response yet) and is filled by
    update_stm32_ack() once the STM32H5 ACK or REJECT arrives.
  - fetch_event() is the only read path on the write side. It exists so the
    oversight tier can hash the row exactly as stored, rather than hashing
    the caller's idea of what it wrote.

Actors:
  'ai'              inference-initiated events from the perception pipeline
  'human_override'  operator action, including the UNO R4 WiFi override button
  'oversight'       machine-initiated action by the UNO R4 WiFi supervisor
                    (governance heartbeat lost, attestation mismatch)
"""

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Actor = Literal["ai", "human_override", "oversight"]
DetectionType = Literal["object", "gesture", "pose", "oversight"]

_SCHEMA = Path(__file__).parent / "schema.sql"
_VALID_ACTORS: frozenset[str] = frozenset({"ai", "human_override", "oversight"})
_VALID_DETECTION_TYPES: frozenset[str] = frozenset(
    {"object", "gesture", "pose", "oversight"}
)


@dataclass(frozen=True)
class AuditEvent:
    session_id: str
    actor: Actor
    detection_type: DetectionType
    detection_label: str
    confidence: float
    command: str
    command_sent: bool
    stm32_ack: bool | None = None
    flag: bool = False
    notes: str | None = None


class AuditLogger:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._conn = self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA.read_text())
        return conn

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def open_session(
        self,
        board_serial: str | None = None,
        notes: str | None = None,
    ) -> str:
        session_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO sessions (session_id, started_at, board_serial, notes)"
            " VALUES (?, ?, ?, ?)",
            (session_id, _now(), board_serial, notes),
        )
        return session_id

    def close_session(self, session_id: str, notes: str | None = None) -> None:
        self._conn.execute(
            "UPDATE sessions SET ended_at = ?, notes = COALESCE(?, notes)"
            " WHERE session_id = ?",
            (_now(), notes, session_id),
        )

    # ------------------------------------------------------------------
    # Audit log write path
    # ------------------------------------------------------------------

    def log_event(self, event: AuditEvent) -> int:
        """Insert one audit record and return its row ID.

        The caller must use the returned ID as audit_ref in the subsequent
        COMMAND_REQUEST IPC frame before that frame is transmitted.
        """
        _validate(event)
        cur = self._conn.execute(
            "INSERT INTO audit_log"
            " (ts, session_id, actor, detection_type, detection_label,"
            "  confidence, command, command_sent, stm32_ack, flag, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now(),
                event.session_id,
                event.actor,
                event.detection_type,
                event.detection_label,
                event.confidence,
                event.command,
                int(event.command_sent),
                None if event.stm32_ack is None else int(event.stm32_ack),
                int(event.flag),
                event.notes,
            ),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def fetch_event(self, event_id: int) -> sqlite3.Row | None:
        """Read back one audit row, or None if the id does not exist.

        Read-only, and the only SELECT on the write path. The oversight tier
        uses it to fold the stored row into the attestation hash chain, so
        that the chain commits to what SQLite actually holds.
        """
        # The column list is written out rather than interpolated: the query
        # is a fixed string, so there is no construction step to get wrong.
        # It excludes stm32_ack and flag, both written after the row is
        # created, because the attestation chain must not commit to them.
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            "SELECT id, ts, session_id, actor, detection_type, detection_label,"
            " confidence, command, command_sent FROM audit_log WHERE id = ?",
            (event_id,),
        )
        row: sqlite3.Row | None = cur.fetchone()
        cur.close()
        return row

    def update_stm32_ack(self, event_id: int, ack: bool) -> None:
        """Record the STM32H5 COMMAND_ACK (True) or COMMAND_REJECT (False).

        Called after the IPC response is received. stm32_ack is NULL until
        this is called; once set it is not overwritten.
        """
        self._conn.execute(
            "UPDATE audit_log SET stm32_ack = ? WHERE id = ? AND stm32_ack IS NULL",
            (int(ack), event_id),
        )

    def flag_event(self, event_id: int, notes: str | None = None) -> None:
        """Mark an event for human review. One-way: flag moves 0 → 1 only."""
        self._conn.execute(
            "UPDATE audit_log SET flag = 1, notes = COALESCE(?, notes) WHERE id = ?",
            (notes, event_id),
        )

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AuditLogger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _validate(event: AuditEvent) -> None:
    if event.actor not in _VALID_ACTORS:
        raise ValueError(
            f"actor must be one of {sorted(_VALID_ACTORS)!r}, got {event.actor!r}"
        )
    if event.detection_type not in _VALID_DETECTION_TYPES:
        raise ValueError(
            f"detection_type must be one of {sorted(_VALID_DETECTION_TYPES)!r},"
            f" got {event.detection_type!r}"
        )
    if not 0.0 <= event.confidence <= 1.0:
        raise ValueError(
            f"confidence must be in [0.0, 1.0], got {event.confidence}"
        )
