"""
Audit log attestation: a rolling SHA-256 hash chain over audit_log rows.

Why this exists
---------------
The SQLite audit log is append-only by construction of the write path, but
that constraint lives on the same host as the governance service. Anyone who
can reach the file can rewrite it, and nothing in the file itself would show
that a row had changed.

The chain closes that gap. Each row folds into a digest:

    digest_0 = GENESIS                                  (32 zero bytes)
    digest_n = SHA-256( digest_{n-1} || canonical(row_n) )

The VENTUNO Q publishes each new head to the UNO R4 WiFi oversight node as it
is produced. The R4 retains the digests in its own memory, off the governance
host. Recomputing the chain from the database later and comparing it against
the retained digests detects any retroactive edit, deletion, or reordering,
because the first altered row changes every digest after it.

What this is not: the chain is unkeyed. An attacker who controls the
governance host and the link to the R4 can forge a consistent chain over
falsified rows going forward. It detects tampering with rows already
witnessed, which is the property the audit argument needs. Signing the
digests with a key held only by the R4 is the next increment.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DIGEST_BYTES: int = 32
GENESIS: bytes = b"\x00" * DIGEST_BYTES

# Field separator that cannot appear in any canonical field rendering.
_SEP: bytes = b"\x1f"


@dataclass(frozen=True)
class AuditRow:
    """The subset of an audit_log row that the hash chain commits to.

    `flag` and `stm32_ack` are deliberately excluded: both are written after
    the row is created (a flag is a reviewer annotation, stm32_ack arrives
    with the MCU response), so including them would break the chain on every
    legitimate update. The chain covers what the row asserted at the moment
    the command decision was taken.
    """

    audit_ref: int
    ts: str
    session_id: str
    actor: str
    detection_type: str
    detection_label: str
    confidence: float
    command: str
    command_sent: bool

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> AuditRow:
        """Build from a sqlite3.Row or any mapping with audit_log column names.

        Accepts either `audit_ref` or the raw column name `id`.
        """
        # sqlite3.Row iterates over values, not keys, so `in row` would test
        # the wrong thing: .keys() is the correct membership check here.
        keys = row.keys()
        ref = row["audit_ref"] if "audit_ref" in keys else row["id"]
        return cls(
            audit_ref=int(ref),
            ts=str(row["ts"]),
            session_id=str(row["session_id"]),
            actor=str(row["actor"]),
            detection_type=str(row["detection_type"]),
            detection_label=str(row["detection_label"]),
            confidence=float(row["confidence"]),
            command=str(row["command"]),
            command_sent=bool(row["command_sent"]),
        )

    def canonical(self) -> bytes:
        """Deterministic byte rendering of the row.

        `confidence` uses %.17g: the shortest form that round-trips a float64
        exactly, so the same stored value always hashes to the same bytes.
        """
        fields = (
            str(self.audit_ref),
            self.ts,
            self.session_id,
            self.actor,
            self.detection_type,
            self.detection_label,
            f"{self.confidence:.17g}",
            self.command,
            "1" if self.command_sent else "0",
        )
        return _SEP.join(f.encode("utf-8") for f in fields)


def chain_step(previous: bytes, row: AuditRow) -> bytes:
    """Fold one row into the chain and return the new head."""
    if len(previous) != DIGEST_BYTES:
        raise ValueError(f"previous digest must be {DIGEST_BYTES} bytes, got {len(previous)}")
    return hashlib.sha256(previous + row.canonical()).digest()


class AuditChain:
    """Incremental hash chain over audit_log rows, in rowid order.

    The chain rejects out-of-order and repeated references: the audit log is
    append-only with an autoincrement key, so a row that does not follow its
    predecessor is evidence of a problem, not something to absorb silently.
    """

    def __init__(self, head: bytes = GENESIS, last_ref: int = 0, count: int = 0) -> None:
        if len(head) != DIGEST_BYTES:
            raise ValueError(f"head must be {DIGEST_BYTES} bytes, got {len(head)}")
        self._head = head
        self._last_ref = last_ref
        self._count = count

    @property
    def head(self) -> bytes:
        """Current chain head: the digest published to the oversight node."""
        return self._head

    @property
    def last_ref(self) -> int:
        """audit_ref of the most recently folded row (0 before the first)."""
        return self._last_ref

    @property
    def count(self) -> int:
        """Number of rows folded into this chain."""
        return self._count

    def append(self, row: AuditRow) -> bytes:
        """Fold one row in and return the new head."""
        if row.audit_ref <= self._last_ref:
            raise ValueError(
                f"audit_ref must increase: got {row.audit_ref} after {self._last_ref}"
            )
        self._head = chain_step(self._head, row)
        self._last_ref = row.audit_ref
        self._count += 1
        return self._head

    @classmethod
    def from_rows(cls, rows: Iterable[AuditRow]) -> AuditChain:
        """Recompute a chain from rows already in rowid order."""
        chain = cls()
        for row in rows:
            chain.append(row)
        return chain


# ---------------------------------------------------------------------------
# Verification against a database
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChainVerification:
    """Result of recomputing the chain over a database."""

    ok: bool
    row_count: int
    head: bytes
    contiguous: bool
    first_missing_ref: int | None
    mismatches: tuple[int, ...]
    reason: str

    def __bool__(self) -> bool:
        return self.ok


def read_rows(conn: sqlite3.Connection) -> list[AuditRow]:
    """Read every audit_log row in rowid order."""
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT id, ts, session_id, actor, detection_type, detection_label,"
        " confidence, command, command_sent FROM audit_log ORDER BY id ASC"
    )
    return [AuditRow.from_mapping(r) for r in cur.fetchall()]


def find_gap(rows: Sequence[AuditRow]) -> int | None:
    """Return the first absent rowid, or None if ids run 1..N without gaps.

    A gap means rows were deleted: AUTOINCREMENT never reuses a rowid.
    """
    expected = 1
    for row in rows:
        if row.audit_ref != expected:
            return expected
        expected += 1
    return None


def verify_database(
    conn: sqlite3.Connection,
    retained: Sequence[tuple[int, bytes]] = (),
) -> ChainVerification:
    """Recompute the chain over the database and reconcile it with the digests
    the oversight node retained independently.

    Parameters
    ----------
    conn:
        Open connection to the audit database.
    retained:
        (audit_ref, digest) pairs read back from the UNO R4 WiFi. Empty means
        recompute only: structural checks still run, but no independent
        witness is available to compare against.

    Returns
    -------
    ChainVerification with `ok` False if any row was deleted, if a retained
    digest does not match the recomputed head at that audit_ref, or if the
    oversight node witnessed a row the database no longer contains.
    """
    rows = read_rows(conn)
    missing = find_gap(rows)

    chain = AuditChain()
    heads: dict[int, bytes] = {}
    for row in rows:
        heads[row.audit_ref] = chain.append(row)

    mismatches: list[int] = []
    absent: list[int] = []
    for ref, digest in retained:
        recomputed = heads.get(ref)
        if recomputed is None:
            absent.append(ref)
        elif recomputed != digest:
            mismatches.append(ref)

    if missing is not None:
        reason = f"row {missing} is absent: the log has been truncated or a row deleted"
    elif absent:
        reason = (
            f"the oversight node witnessed audit_ref {absent[0]}, "
            "which the database no longer contains"
        )
    elif mismatches:
        reason = (
            f"recomputed head differs from the retained digest at audit_ref "
            f"{mismatches[0]}: the row was altered after it was witnessed"
        )
    else:
        reason = (
            f"chain intact over {len(rows)} rows"
            + (f", {len(retained)} witnessed digests reconciled" if retained else "")
        )

    return ChainVerification(
        ok=missing is None and not mismatches and not absent,
        row_count=len(rows),
        head=chain.head,
        contiguous=missing is None,
        first_missing_ref=missing,
        mismatches=tuple(mismatches + absent),
        reason=reason,
    )
