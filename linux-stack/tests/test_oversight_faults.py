"""
Fault-path tests for the oversight tier and the links either side of it.

The happy paths are covered elsewhere. These exercise what happens when a
cable is pulled, a peer disappears mid-read, or the governance host publishes
something that does not follow: the conditions a governance argument stands
or falls on, and the ones that never occur on a bench.
"""

import contextlib
import os
import sqlite3
import threading
import time

import pytest
from logger import AuditLogger

from governance.filter import GovernanceFilter
from ipc.codec import (
    AttestDigest,
    AttestVerdict,
    OverrideReason,
    SupervisorHeartbeat,
    SystemState,
    encode,
)
from oversight.attestation import AuditRow
from oversight.mock_supervisor import MockR4Supervisor
from oversight.supervisor_link import SupervisorLink
from perception.base import DetectionResult

SETTLE_S = 0.15


def _row(ref: int) -> AuditRow:
    return AuditRow(
        audit_ref=ref, ts="2026-08-19T10:00:00.000000+00:00",
        session_id="s", actor="ai", detection_type="object",
        detection_label="person", confidence=0.91,
        command="HALT", command_sent=True,
    )


def _dead_pty():
    """A pty master whose slave has been closed.

    select() reports it readable and read() then fails with EIO: what a USB
    serial device looks like the moment it is unplugged.
    """
    master, slave = os.openpty()
    os.close(slave)
    return master


# ---------------------------------------------------------------------------
# SupervisorLink: publishing a row that does not follow the chain
# ---------------------------------------------------------------------------

class TestLocalChainFault:
    def test_repeated_reference_raises_an_override(self):
        rfd, wfd = os.pipe()
        link = SupervisorLink(open(wfd, "wb", buffering=0), heartbeat_interval_s=0.0)
        try:
            link.record(_row(1))
            link.record(_row(1))   # the same row published twice
            assert link.override_active is True
            assert link.override_reason is OverrideReason.ATTESTATION_MISMATCH
        finally:
            link.close()
            os.close(rfd)

    def test_faulty_row_is_not_counted(self):
        rfd, wfd = os.pipe()
        link = SupervisorLink(open(wfd, "wb", buffering=0), heartbeat_interval_s=0.0)
        try:
            link.record(_row(2))
            head_before = link.chain_head
            link.record(_row(1))   # rewound reference
            assert link.events_logged == 1
            assert link.chain_head == head_before
        finally:
            link.close()
            os.close(rfd)

    def test_faulty_row_publishes_nothing(self):
        """Forwarding a digest that does not follow would corrupt the witness."""
        node_read, link_write = os.pipe()
        link = SupervisorLink(
            open(link_write, "wb", buffering=0), heartbeat_interval_s=100.0
        )
        try:
            link.record(_row(1))
            first = os.read(node_read, 4096)
            link.record(_row(1))
            os.set_blocking(node_read, False)
            try:
                second = os.read(node_read, 4096)
            except BlockingIOError:
                second = b""
            assert first
            assert second == b""
        finally:
            link.close()
            os.close(node_read)

    def test_governance_filter_survives_a_chain_fault(self, tmp_path):
        """A chain fault must veto, not crash the governance loop."""
        db = tmp_path / "audit.db"
        audit_logger = AuditLogger(db)
        session_id = audit_logger.open_session()
        rfd, wfd = os.pipe()
        actuation_r, actuation_w = os.pipe()
        link = SupervisorLink(
            open(wfd, "wb", buffering=0), heartbeat_interval_s=0.0, fail_closed=False
        )
        gf = GovernanceFilter(
            logger=audit_logger, session_id=session_id,
            channel=open(actuation_w, "wb", buffering=0),
            response_timeout_s=0.01, supervisor=link,
        )
        try:
            link.chain.append(_row(1000))       # chain ahead of the database
            gf.process_frame([DetectionResult("object", "person", 0.99)])
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM audit_log").fetchone()
            conn.close()
            assert row["command_sent"] == 1     # the fault is seen after logging
            assert link.override_active is True
            # The next frame is vetoed, which is the point.
            gf.process_frame([DetectionResult("object", "person", 0.99)])
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            second = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            assert second["command_sent"] == 0
            assert "ATTESTATION_MISMATCH" in second["notes"]
        finally:
            link.close()
            os.close(rfd)
            os.close(actuation_r)
            audit_logger.close()


# ---------------------------------------------------------------------------
# SupervisorLink: the channel itself failing
# ---------------------------------------------------------------------------

class TestChannelFailure:
    def test_select_on_a_closed_descriptor_is_survivable(self):
        rfd, wfd = os.pipe()
        channel = open(rfd, "rb", buffering=0)
        link = SupervisorLink(channel, fail_closed=False)
        os.close(rfd)          # closed underneath the file object
        try:
            assert link.poll() is False   # must not raise
        finally:
            os.close(wfd)

    def test_read_error_mid_poll_is_survivable(self):
        """The node is unplugged: readable, but every read fails."""
        master = _dead_pty()
        link = SupervisorLink(open(master, "rb", buffering=0), fail_closed=False)
        try:
            assert link.poll() is False
        finally:
            link.close()

    def test_close_is_idempotent(self):
        rfd, wfd = os.pipe()
        link = SupervisorLink(open(rfd, "rb", buffering=0), fail_closed=False)
        link.close()
        link.close()
        os.close(wfd)

    def test_heartbeat_to_a_broken_pipe_does_not_raise(self):
        rfd, wfd = os.pipe()
        link = SupervisorLink(open(wfd, "wb", buffering=0))
        os.close(rfd)
        try:
            assert link.heartbeat(force=True) is True   # swallowed, not raised
        finally:
            link.close()


# ---------------------------------------------------------------------------
# MockR4Supervisor: resynchronising and shutting down
# ---------------------------------------------------------------------------

class TestSupervisorResync:
    def test_clearing_an_attestation_override_resyncs(self):
        """Otherwise every later digest gaps against a stale expectation and
        the node could never resume. Parity with the C++ port."""
        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as node:
            channel = open(node.device, "rb+", buffering=0)
            try:
                channel.write(encode(AttestDigest(1, b"\x01" * 32)))
                time.sleep(SETTLE_S)
                channel.write(encode(AttestDigest(5, b"\x05" * 32)))  # gap
                time.sleep(SETTLE_S)
                assert node.override_reason is OverrideReason.ATTESTATION_MISMATCH

                channel.write(encode(SupervisorHeartbeat(
                    last_audit_ref=5, system_state=SystemState.ARMED,
                    events_logged=5, commands_sent=1,
                )))
                time.sleep(SETTLE_S)
                assert node.clear_override() is True

                channel.write(encode(AttestDigest(6, b"\x06" * 32)))
                time.sleep(SETTLE_S)
                assert node.override_active is False
                assert [ref for ref, _ in node.retained_digests] == [1, 6]
            finally:
                channel.close()

    def test_the_gap_stays_visible_after_a_clear(self):
        """Accepting an override does not erase what the node witnessed."""
        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as node:
            channel = open(node.device, "rb+", buffering=0)
            try:
                channel.write(encode(AttestDigest(1, b"\x01" * 32)))
                time.sleep(SETTLE_S)
                channel.write(encode(AttestDigest(9, b"\x09" * 32)))
                time.sleep(SETTLE_S)
                channel.write(encode(SupervisorHeartbeat(
                    last_audit_ref=9, system_state=SystemState.ARMED,
                    events_logged=9, commands_sent=0,
                )))
                time.sleep(SETTLE_S)
                node.clear_override()
                assert node.stats.chain_faults == 1
                assert [ref for ref, _ in node.retained_digests] == [1]
            finally:
                channel.close()

    def test_reader_thread_exits_when_the_pty_dies(self):
        node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
        try:
            os.close(node._master_fd)  # noqa: SLF001 - simulating a dead device
            deadline = time.monotonic() + 2.0
            thread = node._thread       # noqa: SLF001
            while thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not thread.is_alive()
        finally:
            node._master_fd = os.open(os.devnull, os.O_RDWR)  # noqa: SLF001
            node.stop()

    def test_watchdog_after_stop_does_nothing(self):
        node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
        node.stop()
        node._on_watchdog_expired()  # noqa: SLF001
        assert node.override_active is False

    def test_write_of_nothing_is_a_no_op(self):
        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as node:
            node._write(None)  # noqa: SLF001
            node._write(b"")   # noqa: SLF001

    def test_clear_refused_before_any_heartbeat(self):
        """A node that has never heard from the governance tier cannot
        conclude the tier is healthy."""
        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as node:
            node.press_button()
            node.release_button()
            assert node.clear_override() is False

    def test_kill_line_held_until_first_contact(self):
        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as node:
            channel = open(node.device, "rb+", buffering=0)
            try:
                assert node.kill_line_asserted is True
                channel.write(encode(SupervisorHeartbeat(
                    last_audit_ref=0, system_state=SystemState.ARMED,
                    events_logged=0, commands_sent=0,
                )))
                time.sleep(SETTLE_S)
                assert node.kill_line_asserted is False
            finally:
                channel.close()

    def test_concurrent_digests_are_serialised(self):
        """The node is driven from a reader thread; state must stay coherent."""
        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as node:
            channel = open(node.device, "rb+", buffering=0)
            try:
                def writer(start: int) -> None:
                    for ref in range(start, start + 10):
                        channel.write(encode(AttestDigest(ref, bytes([ref % 256]) * 32)))
                        time.sleep(0.005)

                threads = [threading.Thread(target=writer, args=(1,))]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                time.sleep(0.3)
                refs = [ref for ref, _ in node.retained_digests]
                assert refs == sorted(refs)
                assert node.stats.digests_received == 10
            finally:
                channel.close()


# ---------------------------------------------------------------------------
# GovernanceFilter: the actuation channel failing
# ---------------------------------------------------------------------------

class TestActuationChannelFailure:
    def test_dead_actuation_channel_leaves_stm32_ack_null(self, tmp_path):
        """The peer is unplugged mid-command. NULL is the honest record."""
        db = tmp_path / "audit.db"
        audit_logger = AuditLogger(db)
        session_id = audit_logger.open_session()
        channel = open(_dead_pty(), "rb+", buffering=0)
        gf = GovernanceFilter(
            logger=audit_logger, session_id=session_id,
            channel=channel, response_timeout_s=0.2,
        )
        try:
            gf.process_frame([DetectionResult("object", "person", 0.91)])
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM audit_log").fetchone()
            conn.close()
            assert row["command_sent"] == 1
            assert row["stm32_ack"] is None
        finally:
            audit_logger.close()

    def test_failed_transmit_flags_the_event(self, tmp_path):
        """The row already claims command_sent=1 and the log is append-only,
        so the flag is the only honest way to say the frame never went out."""
        db = tmp_path / "audit.db"
        audit_logger = AuditLogger(db)
        session_id = audit_logger.open_session()
        rfd, wfd = os.pipe()
        channel = open(wfd, "wb", buffering=0)
        gf = GovernanceFilter(
            logger=audit_logger, session_id=session_id,
            channel=channel, response_timeout_s=0.1,
        )
        os.close(rfd)   # every write now fails with EPIPE
        try:
            gf.process_frame([DetectionResult("object", "person", 0.91)])
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM audit_log").fetchone()
            conn.close()
            assert row["command_sent"] == 1
            assert row["stm32_ack"] is None
            assert row["flag"] == 1
            assert "transmit failed" in row["notes"]
        finally:
            audit_logger.close()

    def test_eof_on_the_actuation_channel_leaves_stm32_ack_null(self, tmp_path):
        db = tmp_path / "audit.db"
        audit_logger = AuditLogger(db)
        session_id = audit_logger.open_session()
        read_fd, write_fd = os.pipe()
        out_r, out_w = os.pipe()

        class _Split:
            """Reads from one pipe, writes to another: the peer hung up."""

            def __init__(self) -> None:
                self._r = read_fd

            def write(self, data: bytes) -> int:
                return os.write(out_w, data)

            def fileno(self) -> int:
                return self._r

        gf = GovernanceFilter(
            logger=audit_logger, session_id=session_id,
            channel=_Split(), response_timeout_s=0.5,
        )
        os.close(write_fd)   # EOF on the read side
        try:
            gf.process_frame([DetectionResult("object", "person", 0.91)])
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM audit_log").fetchone()
            conn.close()
            assert row["stm32_ack"] is None
        finally:
            for fd in (read_fd, out_r, out_w):
                with contextlib.suppress(OSError):
                    os.close(fd)
            audit_logger.close()


# ---------------------------------------------------------------------------
# Verdict handling on the link
# ---------------------------------------------------------------------------

class TestVerdictHandling:
    def test_chain_ok_does_not_raise_an_override(self):
        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as node:
            link = SupervisorLink(
                open(node.device, "rb+", buffering=0), heartbeat_interval_s=0.0
            )
            try:
                link.record(_row(1))
                time.sleep(SETTLE_S)
                assert link.poll() is False
                assert link.last_verdict is AttestVerdict.CHAIN_OK
            finally:
                link.close()

    @pytest.mark.parametrize(
        "verdict", [AttestVerdict.GAP, AttestVerdict.CHAIN_BREAK]
    )
    def test_bad_verdicts_raise_an_override(self, verdict):
        from ipc.codec import AttestAck

        link_read, node_write = os.pipe()
        link = SupervisorLink(open(link_read, "rb", buffering=0), fail_closed=False)
        try:
            os.write(node_write, encode(AttestAck(audit_ref=1, verdict=verdict)))
            assert link.poll() is True
            assert link.override_reason is OverrideReason.ATTESTATION_MISMATCH
        finally:
            link.close()
            os.close(node_write)
