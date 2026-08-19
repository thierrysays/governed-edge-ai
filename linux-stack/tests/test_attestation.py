"""
Unit tests for oversight/attestation.py: the audit log hash chain.

The point of the chain is detection of retroactive edits, so most of these
tests tamper with a database and assert that verification notices.
"""

import sqlite3

import pytest
from logger import AuditEvent, AuditLogger

from oversight.attestation import (
    DIGEST_BYTES,
    GENESIS,
    AuditChain,
    AuditRow,
    chain_step,
    find_gap,
    read_rows,
    verify_database,
)


def _row(ref: int = 1, label: str = "person", confidence: float = 0.91) -> AuditRow:
    return AuditRow(
        audit_ref=ref,
        ts="2026-08-19T10:00:00.000000+00:00",
        session_id="session-1",
        actor="ai",
        detection_type="object",
        detection_label=label,
        confidence=confidence,
        command="HALT",
        command_sent=True,
    )


@pytest.fixture
def seeded_db(tmp_path):
    """An audit database with five rows written through the real write path."""
    db = tmp_path / "audit.db"
    logger = AuditLogger(db)
    session = logger.open_session(board_serial="R4-TEST")
    for i in range(5):
        logger.log_event(AuditEvent(
            session_id=session,
            actor="ai",
            detection_type="object",
            detection_label="person",
            confidence=0.80 + i / 100,
            command="HALT",
            command_sent=(i == 0),
        ))
    logger.close()
    return db


def _connect(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Canonical rendering
# ---------------------------------------------------------------------------

class TestCanonical:
    def test_deterministic(self):
        assert _row().canonical() == _row().canonical()

    def test_label_change_changes_bytes(self):
        assert _row(label="person").canonical() != _row(label="tool").canonical()

    def test_confidence_change_changes_bytes(self):
        assert _row(confidence=0.91).canonical() != _row(confidence=0.92).canonical()

    def test_confidence_round_trips_exactly(self):
        """%.17g must distinguish two float64 values one ulp apart."""
        import math
        a = 0.7
        b = math.nextafter(0.7, 1.0)
        assert _row(confidence=a).canonical() != _row(confidence=b).canonical()

    def test_command_sent_is_part_of_the_commitment(self):
        sent = _row()
        suppressed = AuditRow(**{**vars(sent), "command_sent": False})
        assert sent.canonical() != suppressed.canonical()

    def test_fields_cannot_be_confused_across_the_separator(self):
        """Moving a character between adjacent fields must change the digest."""
        a = AuditRow(**{**vars(_row()), "detection_label": "ab", "command": "cd"})
        b = AuditRow(**{**vars(_row()), "detection_label": "a", "command": "bcd"})
        assert a.canonical() != b.canonical()

    def test_from_mapping_accepts_id_column(self):
        row = AuditRow.from_mapping({
            "id": 7, "ts": "t", "session_id": "s", "actor": "ai",
            "detection_type": "object", "detection_label": "person",
            "confidence": 0.9, "command": "HALT", "command_sent": 1,
        })
        assert row.audit_ref == 7
        assert row.command_sent is True

    def test_from_mapping_accepts_audit_ref_column(self):
        row = AuditRow.from_mapping({
            "audit_ref": 3, "ts": "t", "session_id": "s", "actor": "oversight",
            "detection_type": "oversight", "detection_label": "operator_override",
            "confidence": 1.0, "command": "HALT", "command_sent": 0,
        })
        assert row.audit_ref == 3
        assert row.command_sent is False


# ---------------------------------------------------------------------------
# Chain mechanics
# ---------------------------------------------------------------------------

class TestChain:
    def test_genesis_is_32_zero_bytes(self):
        assert GENESIS == b"\x00" * DIGEST_BYTES

    def test_head_starts_at_genesis(self):
        assert AuditChain().head == GENESIS

    def test_append_returns_new_head(self):
        chain = AuditChain()
        head = chain.append(_row(1))
        assert head == chain.head
        assert len(head) == DIGEST_BYTES
        assert head != GENESIS

    def test_counters_advance(self):
        chain = AuditChain()
        chain.append(_row(1))
        chain.append(_row(2))
        assert chain.count == 2
        assert chain.last_ref == 2

    def test_same_rows_give_same_head(self):
        a = AuditChain.from_rows([_row(1), _row(2), _row(3)])
        b = AuditChain.from_rows([_row(1), _row(2), _row(3)])
        assert a.head == b.head

    def test_one_altered_row_changes_the_head(self):
        clean = AuditChain.from_rows([_row(1), _row(2), _row(3)])
        tampered = AuditChain.from_rows([_row(1), _row(2, label="tool"), _row(3)])
        assert clean.head != tampered.head

    def test_reordering_changes_the_head(self):
        a = AuditChain.from_rows([_row(1, label="person"), _row(2, label="tool")])
        b = AuditChain.from_rows([_row(1, label="tool"), _row(2, label="person")])
        assert a.head != b.head

    def test_refuses_repeated_reference(self):
        chain = AuditChain()
        chain.append(_row(1))
        with pytest.raises(ValueError, match="must increase"):
            chain.append(_row(1))

    def test_refuses_rewound_reference(self):
        chain = AuditChain()
        chain.append(_row(5))
        with pytest.raises(ValueError, match="must increase"):
            chain.append(_row(4))

    def test_gap_is_accepted_by_the_chain_itself(self):
        """The chain folds what it is given; gap detection is the R4's job."""
        chain = AuditChain()
        chain.append(_row(1))
        chain.append(_row(9))
        assert chain.count == 2

    def test_bad_head_length_rejected(self):
        with pytest.raises(ValueError, match="32 bytes"):
            AuditChain(head=b"\x00" * 31)

    def test_chain_step_rejects_bad_previous(self):
        with pytest.raises(ValueError, match="32 bytes"):
            chain_step(b"\x00", _row())

    def test_resume_from_a_saved_head(self):
        full = AuditChain.from_rows([_row(1), _row(2), _row(3)])
        partial = AuditChain.from_rows([_row(1), _row(2)])
        resumed = AuditChain(head=partial.head, last_ref=partial.last_ref, count=2)
        resumed.append(_row(3))
        assert resumed.head == full.head


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

class TestFindGap:
    def test_no_rows_no_gap(self):
        assert find_gap([]) is None

    def test_contiguous(self):
        assert find_gap([_row(1), _row(2), _row(3)]) is None

    def test_missing_middle_row(self):
        assert find_gap([_row(1), _row(3)]) == 2

    def test_missing_first_row(self):
        assert find_gap([_row(2), _row(3)]) == 1


# ---------------------------------------------------------------------------
# Verification against a real database
# ---------------------------------------------------------------------------

class TestVerifyDatabase:
    def test_clean_database_verifies(self, seeded_db):
        with _connect(seeded_db) as conn:
            result = verify_database(conn)
        assert result.ok
        assert bool(result) is True
        assert result.row_count == 5
        assert result.contiguous
        assert "chain intact over 5 rows" in result.reason

    def test_recompute_is_stable(self, seeded_db):
        with _connect(seeded_db) as conn:
            first = verify_database(conn)
            second = verify_database(conn)
        assert first.head == second.head

    def test_retained_digests_reconcile(self, seeded_db):
        with _connect(seeded_db) as conn:
            rows = read_rows(conn)
            chain = AuditChain()
            witnessed = [(r.audit_ref, chain.append(r)) for r in rows]
            result = verify_database(conn, retained=witnessed)
        assert result.ok
        assert "5 witnessed digests reconciled" in result.reason

    def test_altered_row_detected(self, seeded_db):
        """The payoff: an edit the append-only write path would never make."""
        with _connect(seeded_db) as conn:
            rows = read_rows(conn)
            chain = AuditChain()
            witnessed = [(r.audit_ref, chain.append(r)) for r in rows]

        with _connect(seeded_db) as conn:
            conn.execute("UPDATE audit_log SET confidence = 0.10 WHERE id = 3")
            conn.commit()
            result = verify_database(conn, retained=witnessed)

        assert not result.ok
        assert result.contiguous          # nothing was deleted
        assert 3 in result.mismatches     # but row 3 no longer hashes the same
        assert "altered after it was witnessed" in result.reason

    def test_deleted_row_detected_without_any_witness(self, seeded_db):
        """A gap in the rowid sequence is proof on its own: AUTOINCREMENT
        never reuses an id, so the log cannot legitimately skip one."""
        with _connect(seeded_db) as conn:
            conn.execute("DELETE FROM audit_log WHERE id = 2")
            conn.commit()
            result = verify_database(conn)
        assert not result.ok
        assert not result.contiguous
        assert result.first_missing_ref == 2
        assert "truncated or a row deleted" in result.reason

    def test_truncated_tail_detected_by_the_witness(self, seeded_db):
        """Deleting the last row leaves ids contiguous. Only the oversight
        node's copy of the digest shows that row 5 ever existed."""
        with _connect(seeded_db) as conn:
            rows = read_rows(conn)
            chain = AuditChain()
            witnessed = [(r.audit_ref, chain.append(r)) for r in rows]

        with _connect(seeded_db) as conn:
            conn.execute("DELETE FROM audit_log WHERE id = 5")
            conn.commit()
            unwitnessed = verify_database(conn)
            witnessed_result = verify_database(conn, retained=witnessed)

        assert unwitnessed.ok                 # the file alone looks fine
        assert not witnessed_result.ok        # the independent witness does not
        assert "no longer contains" in witnessed_result.reason

    def test_stm32_ack_update_does_not_break_the_chain(self, seeded_db):
        """stm32_ack is written after the row; the chain must not commit to it."""
        with _connect(seeded_db) as conn:
            rows = read_rows(conn)
            chain = AuditChain()
            witnessed = [(r.audit_ref, chain.append(r)) for r in rows]

        logger = AuditLogger(seeded_db)
        logger.update_stm32_ack(1, True)
        logger.flag_event(2, notes="reviewed")
        logger.close()

        with _connect(seeded_db) as conn:
            result = verify_database(conn, retained=witnessed)
        assert result.ok

    def test_empty_database_verifies(self, tmp_path):
        logger = AuditLogger(tmp_path / "empty.db")
        logger.close()
        with _connect(tmp_path / "empty.db") as conn:
            result = verify_database(conn)
        assert result.ok
        assert result.row_count == 0
        assert result.head == GENESIS
