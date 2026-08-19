"""
Unit tests for oversight/supervisor_link.py: the VENTUNO Q side of the
oversight link.

Most tests wire a real SupervisorLink to a real MockR4Supervisor over a pty,
so the two halves of the protocol are exercised against each other rather
than against a stub of either.
"""

import os
import time

import pytest

from ipc.codec import (
    AttestAck,
    AttestVerdict,
    FrameParser,
    OverrideAssert,
    OverrideClear,
    OverrideReason,
    SupervisorHeartbeat,
    SystemState,
    encode,
)
from oversight.attestation import GENESIS, AuditChain, AuditRow
from oversight.mock_supervisor import MockR4Supervisor
from oversight.supervisor_link import SupervisorLink

SETTLE_S = 0.15


def _row(ref: int) -> AuditRow:
    return AuditRow(
        audit_ref=ref,
        ts=f"2026-08-19T10:00:{ref:02d}.000000+00:00",
        session_id="session-1",
        actor="ai",
        detection_type="object",
        detection_label="person",
        confidence=0.91,
        command="HALT",
        command_sent=True,
    )


@pytest.fixture
def wired():
    """A SupervisorLink connected to a MockR4Supervisor over a pty."""
    node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
    channel = open(node.device, "rb+", buffering=0)
    link = SupervisorLink(channel, heartbeat_interval_s=0.0)
    try:
        yield link, node
    finally:
        link.close()
        node.stop()


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_starts_without_an_override(self, wired):
        link, _ = wired
        assert link.override_active is False
        assert link.override_reason is None

    def test_chain_starts_at_genesis(self, wired):
        link, _ = wired
        assert link.chain_head == GENESIS

    def test_counters_start_at_zero(self, wired):
        link, _ = wired
        assert link.events_logged == 0
        assert link.commands_sent == 0

    def test_link_is_alive_at_construction(self, wired):
        link, _ = wired
        assert link.link_alive is True

    def test_accepts_an_existing_chain(self):
        rfd, wfd = os.pipe()
        chain = AuditChain.from_rows([_row(1), _row(2)])
        link = SupervisorLink(open(wfd, "wb", buffering=0), chain=chain)
        try:
            assert link.chain_head == chain.head
            assert link.chain.last_ref == 2
        finally:
            link.close()
            os.close(rfd)


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_forced_heartbeat_reaches_the_node(self, wired):
        link, node = wired
        assert link.heartbeat(force=True) is True
        time.sleep(SETTLE_S)
        assert node.stats.heartbeats_received >= 1

    def test_heartbeat_carries_the_session_counters(self, wired):
        link, node = wired
        link.record(_row(1), command_sent=True)
        link.record(_row(2), command_sent=False)
        link.heartbeat(force=True)
        time.sleep(SETTLE_S)
        hb = node.last_heartbeat
        assert hb.events_logged == 2
        assert hb.commands_sent == 1
        assert hb.last_audit_ref == 2

    def test_rate_limited_between_intervals(self):
        rfd, wfd = os.pipe()
        link = SupervisorLink(open(wfd, "wb", buffering=0), heartbeat_interval_s=10.0)
        try:
            assert link.heartbeat(force=True) is True
            assert link.heartbeat() is False
        finally:
            link.close()
            os.close(rfd)

    def test_force_ignores_the_interval(self):
        rfd, wfd = os.pipe()
        link = SupervisorLink(open(wfd, "wb", buffering=0), heartbeat_interval_s=10.0)
        try:
            link.heartbeat(force=True)
            assert link.heartbeat(force=True) is True
        finally:
            link.close()
            os.close(rfd)


# ---------------------------------------------------------------------------
# Publishing chain digests
# ---------------------------------------------------------------------------

class TestRecord:
    def test_record_advances_the_chain(self, wired):
        link, _ = wired
        head = link.record(_row(1))
        assert head != GENESIS
        assert link.chain_head == head

    def test_record_matches_an_independently_computed_chain(self, wired):
        link, _ = wired
        for ref in (1, 2, 3):
            link.record(_row(ref))
        expected = AuditChain.from_rows([_row(1), _row(2), _row(3)])
        assert link.chain_head == expected.head

    def test_node_retains_the_published_digest(self, wired):
        link, node = wired
        head = link.record(_row(1))
        time.sleep(SETTLE_S)
        assert node.retained_digests == [(1, head)]

    def test_node_acks_chain_ok(self, wired):
        link, _ = wired
        link.record(_row(1))
        time.sleep(SETTLE_S)
        link.poll()
        assert link.last_verdict is AttestVerdict.CHAIN_OK
        assert link.override_active is False

    def test_command_sent_increments_only_when_true(self, wired):
        link, _ = wired
        link.record(_row(1), command_sent=True)
        link.record(_row(2), command_sent=False)
        assert link.events_logged == 2
        assert link.commands_sent == 1

    def test_write_failure_does_not_propagate(self):
        """The oversight node going dark must not take governance down."""
        rfd, wfd = os.pipe()
        channel = open(wfd, "wb", buffering=0)
        link = SupervisorLink(channel, heartbeat_interval_s=0.0)
        os.close(rfd)  # every write now fails with EPIPE
        try:
            link.record(_row(1))  # must not raise
            assert link.chain.count == 1
        finally:
            link.close()


# ---------------------------------------------------------------------------
# Override handling
# ---------------------------------------------------------------------------

class TestOverride:
    def test_button_press_reaches_the_link(self, wired):
        link, node = wired
        node.press_button()
        time.sleep(SETTLE_S)
        assert link.poll() is True
        assert link.override_reason is OverrideReason.OPERATOR_BUTTON

    def test_override_clear_releases_the_link(self, wired):
        link, node = wired
        link.heartbeat(force=True)
        time.sleep(SETTLE_S)
        node.press_button()
        node.release_button()
        time.sleep(SETTLE_S)
        assert link.poll() is True
        node.clear_override()
        time.sleep(SETTLE_S)
        assert link.poll() is False
        assert link.override_reason is None

    def test_gap_verdict_raises_an_override_on_the_link(self, wired):
        """Rows 2..8 never reach the node, as a truncated log would look."""
        link, node = wired
        link.record(_row(1))
        time.sleep(SETTLE_S)
        link.poll()
        link.record(_row(9))
        time.sleep(SETTLE_S)
        assert link.poll() is True
        assert link.override_reason is OverrideReason.ATTESTATION_MISMATCH
        assert link.last_verdict is AttestVerdict.GAP

    def test_direct_override_assert_frame_is_honoured(self):
        """The link must act on the frame itself, not only on the mock."""
        link_read, node_write = os.pipe()
        link = SupervisorLink(open(link_read, "rb", buffering=0), fail_closed=False)
        try:
            os.write(node_write, encode(OverrideAssert(
                timestamp_us=1, reason=OverrideReason.REMOTE_CONSOLE,
            )))
            assert link.poll() is True
            assert link.override_reason is OverrideReason.REMOTE_CONSOLE

            os.write(node_write, encode(OverrideClear(timestamp_us=2)))
            assert link.poll() is False
        finally:
            link.close()
            os.close(node_write)

    def test_non_ok_verdict_frame_raises_override(self):
        link_read, node_write = os.pipe()
        link = SupervisorLink(open(link_read, "rb", buffering=0), fail_closed=False)
        try:
            os.write(node_write, encode(AttestAck(
                audit_ref=1, verdict=AttestVerdict.CHAIN_BREAK,
            )))
            assert link.poll() is True
            assert link.last_verdict is AttestVerdict.CHAIN_BREAK
        finally:
            link.close()
            os.close(node_write)

    def test_unrelated_frames_are_ignored(self):
        link_read, node_write = os.pipe()
        link = SupervisorLink(open(link_read, "rb", buffering=0), fail_closed=False)
        try:
            os.write(node_write, encode(SupervisorHeartbeat(
                last_audit_ref=1, system_state=SystemState.ARMED,
                events_logged=1, commands_sent=0,
            )))
            assert link.poll() is False
        finally:
            link.close()
            os.close(node_write)


# ---------------------------------------------------------------------------
# Fail-closed behaviour
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_silent_link_becomes_an_override(self):
        """A supervisor that cannot be reached is not a satisfied supervisor."""
        rfd, wfd = os.pipe()
        link = SupervisorLink(
            open(rfd, "rb", buffering=0), link_timeout_s=0.05, fail_closed=True
        )
        try:
            time.sleep(0.1)
            assert link.poll() is True
            assert link.override_reason is OverrideReason.GOVERNANCE_HEARTBEAT_LOST
            assert link.link_alive is False
        finally:
            link.close()
            os.close(wfd)

    def test_fail_open_tolerates_silence(self):
        rfd, wfd = os.pipe()
        link = SupervisorLink(
            open(rfd, "rb", buffering=0), link_timeout_s=0.05, fail_closed=False
        )
        try:
            time.sleep(0.1)
            assert link.poll() is False
            assert link.link_alive is False
        finally:
            link.close()
            os.close(wfd)

    def test_inbound_traffic_keeps_the_link_alive(self, wired):
        link, node = wired
        link.record(_row(1))     # draws an ATTEST_ACK back
        time.sleep(SETTLE_S)
        link.poll()
        assert link.link_alive is True

    def test_closed_channel_does_not_raise_on_poll(self):
        rfd, wfd = os.pipe()
        channel = open(rfd, "rb", buffering=0)
        link = SupervisorLink(channel, fail_closed=False)
        channel.close()
        os.close(wfd)
        assert link.poll() is False  # must not raise

    def test_eof_on_the_channel_is_survivable(self):
        rfd, wfd = os.pipe()
        link = SupervisorLink(open(rfd, "rb", buffering=0), fail_closed=False)
        try:
            os.close(wfd)  # writer gone: read() returns b""
            assert link.poll() is False
        finally:
            link.close()


# ---------------------------------------------------------------------------
# Framing across chunk boundaries
# ---------------------------------------------------------------------------

class TestFraming:
    def test_frame_split_across_reads(self):
        link_read, node_write = os.pipe()
        link = SupervisorLink(open(link_read, "rb", buffering=0), fail_closed=False)
        try:
            frame = encode(OverrideAssert(
                timestamp_us=1, reason=OverrideReason.OPERATOR_BUTTON,
            ))
            os.write(node_write, frame[:5])
            assert link.poll() is False   # incomplete frame: no decision yet
            os.write(node_write, frame[5:])
            assert link.poll() is True
        finally:
            link.close()
            os.close(node_write)

    def test_garbage_before_a_frame_is_discarded(self):
        link_read, node_write = os.pipe()
        link = SupervisorLink(open(link_read, "rb", buffering=0), fail_closed=False)
        try:
            os.write(node_write, b"\x00\x01\x02" + encode(OverrideAssert(
                timestamp_us=1, reason=OverrideReason.OPERATOR_BUTTON,
            )))
            assert link.poll() is True
        finally:
            link.close()
            os.close(node_write)

    def test_parser_survives_a_corrupt_frame(self):
        link_read, node_write = os.pipe()
        link = SupervisorLink(open(link_read, "rb", buffering=0), fail_closed=False)
        try:
            corrupt = bytearray(encode(OverrideClear(timestamp_us=1)))
            corrupt[-1] ^= 0xFF
            os.write(node_write, bytes(corrupt))
            link.poll()
            os.write(node_write, encode(OverrideAssert(
                timestamp_us=2, reason=OverrideReason.OPERATOR_BUTTON,
            )))
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not link.poll():
                time.sleep(0.01)
            assert link.override_active is True
        finally:
            link.close()
            os.close(node_write)


# ---------------------------------------------------------------------------
# Wire-level shape of what the link emits
# ---------------------------------------------------------------------------

class TestEmittedFrames:
    def test_record_emits_digest_then_heartbeat(self):
        node_read, link_write = os.pipe()
        link = SupervisorLink(
            open(link_write, "wb", buffering=0), heartbeat_interval_s=0.0
        )
        try:
            link.record(_row(1), command_sent=True)
            parser = FrameParser()
            parser.feed(os.read(node_read, 4096))
            msgs = parser.pop_messages()
        finally:
            link.close()
            os.close(node_read)

        assert msgs[0].audit_ref == 1
        assert msgs[0].digest == AuditChain.from_rows([_row(1)]).head
        assert isinstance(msgs[1], SupervisorHeartbeat)
        assert msgs[1].commands_sent == 1
