"""
Unit tests for MockR4Supervisor: the reference model of the UNO R4 WiFi
oversight node.

These tests are the specification the C++ sketch in r4-supervisor/ is held
to. test_r4_firmware_parity.py replays the same scenarios against the
compiled firmware logic.

Every test drives a real pty pair, exactly as the VENTUNO Q would.
"""

import os
import select
import time

import pytest

from ipc.codec import (
    AttestAck,
    AttestDigest,
    AttestVerdict,
    FrameParser,
    OverrideAssert,
    OverrideClear,
    OverrideReason,
    SupervisorHeartbeat,
    SystemState,
    encode,
)
from oversight.mock_supervisor import (
    ANNUNCIATOR_ATTEST_ALERT,
    ANNUNCIATOR_OVERRIDE,
    ANNUNCIATOR_STALE,
    ANNUNCIATOR_WATCHING,
    MockR4Supervisor,
)

SETTLE_S = 0.15


@pytest.fixture
def node():
    """A supervisor with a watchdog long enough not to fire during a test."""
    with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as n:
        yield n


@pytest.fixture
def channel(node):
    ch = open(node.device, "rb+", buffering=0)
    yield ch
    ch.close()


def _read_messages(channel, timeout_s: float = 0.5) -> list:
    """Drain whatever the node has sent, decoding complete frames."""
    parser = FrameParser()
    deadline = time.monotonic() + timeout_s
    fd = channel.fileno()
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            continue
        parser.feed(os.read(fd, 4096))
    return parser.pop_messages()


def _heartbeat(ref: int = 0, events: int = 0, sent: int = 0) -> bytes:
    return encode(SupervisorHeartbeat(
        last_audit_ref=ref, system_state=SystemState.ARMED,
        events_logged=events, commands_sent=sent,
    ))


def _digest(ref: int, fill: int = 0xAB) -> bytes:
    return encode(AttestDigest(audit_ref=ref, digest=bytes([fill]) * 32))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_device_is_a_pty_path(self, node):
        assert node.device.startswith("/dev/pts/")

    def test_starts_watching(self, node):
        assert node.override_active is False
        assert node.override_reason is None
        assert node.annunciator == ANNUNCIATOR_WATCHING

    def test_kill_line_held_before_the_first_heartbeat(self, node):
        """The board comes up holding the line: a governance tier that has
        said nothing has not earned the authority to move a robot."""
        assert node.kill_line_asserted is True
        assert node.override_active is False

    def test_kill_line_releases_on_first_contact(self, node, channel):
        channel.write(_heartbeat())
        time.sleep(SETTLE_S)
        assert node.kill_line_asserted is False

    def test_context_manager_stops_cleanly(self):
        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as n:
            device = n.device
        assert device  # stop() ran without raising

    def test_stop_is_idempotent(self):
        n = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
        n.stop()
        n.stop()


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_is_recorded(self, node, channel):
        channel.write(_heartbeat(ref=7, events=12, sent=3))
        time.sleep(SETTLE_S)
        hb = node.last_heartbeat
        assert hb is not None
        assert hb.last_audit_ref == 7
        assert hb.events_logged == 12
        assert hb.commands_sent == 3
        assert node.stats.heartbeats_received == 1

    def test_heartbeat_draws_no_response(self, node, channel):
        """The oversight node reports nothing back on a healthy heartbeat.
        Silence on this link means the veto is not raised."""
        channel.write(_heartbeat())
        assert _read_messages(channel, timeout_s=0.2) == []

    def test_many_heartbeats_counted(self, node, channel):
        for i in range(5):
            channel.write(_heartbeat(ref=i))
        time.sleep(SETTLE_S)
        assert node.stats.heartbeats_received == 5


# ---------------------------------------------------------------------------
# Watchdog on the governance tier
# ---------------------------------------------------------------------------

class TestWatchdog:
    def test_silence_latches_an_override(self):
        with MockR4Supervisor(heartbeat_timeout_ms=100.0) as node:
            ch = open(node.device, "rb+", buffering=0)
            try:
                time.sleep(0.3)
                assert node.override_active
                assert node.override_reason is OverrideReason.GOVERNANCE_HEARTBEAT_LOST
                assert node.annunciator == ANNUNCIATOR_STALE
                assert node.kill_line_asserted
            finally:
                ch.close()

    def test_override_assert_is_transmitted(self):
        with MockR4Supervisor(heartbeat_timeout_ms=100.0) as node:
            ch = open(node.device, "rb+", buffering=0)
            try:
                time.sleep(0.3)
                msgs = _read_messages(ch, timeout_s=0.2)
            finally:
                ch.close()
        asserts = [m for m in msgs if isinstance(m, OverrideAssert)]
        assert asserts
        assert asserts[0].reason is OverrideReason.GOVERNANCE_HEARTBEAT_LOST

    def test_heartbeats_hold_the_watchdog_off(self):
        with MockR4Supervisor(heartbeat_timeout_ms=250.0) as node:
            ch = open(node.device, "rb+", buffering=0)
            try:
                for _ in range(6):
                    ch.write(_heartbeat())
                    time.sleep(0.05)
                assert node.override_active is False
            finally:
                ch.close()

    def test_watchdog_fires_only_once(self):
        with MockR4Supervisor(heartbeat_timeout_ms=100.0) as node:
            ch = open(node.device, "rb+", buffering=0)
            try:
                time.sleep(0.4)
                assert node.stats.overrides_asserted == 1
            finally:
                ch.close()


# ---------------------------------------------------------------------------
# Physical override button
# ---------------------------------------------------------------------------

class TestButton:
    def test_press_latches_and_transmits(self, node, channel):
        node.press_button()
        msgs = _read_messages(channel, timeout_s=0.3)
        assert node.override_active
        assert node.override_reason is OverrideReason.OPERATOR_BUTTON
        assert node.annunciator == ANNUNCIATOR_OVERRIDE
        assert any(
            isinstance(m, OverrideAssert)
            and m.reason is OverrideReason.OPERATOR_BUTTON
            for m in msgs
        )

    def test_press_asserts_the_kill_line(self, node):
        node.press_button()
        assert node.kill_line_asserted

    def test_release_does_not_clear_the_latch(self, node):
        node.press_button()
        node.release_button()
        assert node.override_active
        assert node.kill_line_asserted

    def test_repeated_press_asserts_once(self, node):
        node.press_button()
        node.press_button()
        assert node.stats.overrides_asserted == 1

    def test_remote_console_override(self, node, channel):
        node.remote_override()
        msgs = _read_messages(channel, timeout_s=0.3)
        assert node.override_reason is OverrideReason.REMOTE_CONSOLE
        assert any(isinstance(m, OverrideAssert) for m in msgs)


# ---------------------------------------------------------------------------
# Clearing an override
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_refused_while_button_held(self, node, channel):
        channel.write(_heartbeat())
        time.sleep(SETTLE_S)
        node.press_button()
        assert node.clear_override() is False
        assert node.override_active

    def test_clear_succeeds_after_release(self, node, channel):
        channel.write(_heartbeat())
        time.sleep(SETTLE_S)
        node.press_button()
        node.release_button()
        assert node.clear_override() is True
        assert node.override_active is False
        assert node.annunciator == ANNUNCIATOR_WATCHING

    def test_clear_transmits_override_clear(self, node, channel):
        channel.write(_heartbeat())
        time.sleep(SETTLE_S)
        node.press_button()
        node.release_button()
        _read_messages(channel, timeout_s=0.1)  # drop the assert
        node.clear_override()
        msgs = _read_messages(channel, timeout_s=0.3)
        assert any(isinstance(m, OverrideClear) for m in msgs)

    def test_clear_refused_while_governance_is_silent(self):
        """An override raised by silence cannot be cleared into that silence."""
        with MockR4Supervisor(heartbeat_timeout_ms=100.0) as node:
            time.sleep(0.3)
            assert node.override_active
            assert node.clear_override() is False

    def test_clear_when_already_watching_is_a_no_op(self, node):
        assert node.clear_override() is True
        assert node.stats.overrides_cleared == 0

    def test_clear_refused_before_any_heartbeat(self, node):
        node.press_button()
        node.release_button()
        assert node.clear_override() is False

    def test_kill_line_released_after_clear(self, node, channel):
        channel.write(_heartbeat())
        time.sleep(SETTLE_S)
        node.press_button()
        node.release_button()
        node.clear_override()
        assert node.kill_line_asserted is False


# ---------------------------------------------------------------------------
# Attestation digests
# ---------------------------------------------------------------------------

class TestAttestation:
    def test_first_digest_acked_ok(self, node, channel):
        channel.write(_digest(1))
        msgs = _read_messages(channel, timeout_s=0.3)
        acks = [m for m in msgs if isinstance(m, AttestAck)]
        assert acks == [AttestAck(audit_ref=1, verdict=AttestVerdict.CHAIN_OK)]

    def test_sequential_digests_retained(self, node, channel):
        for ref in (1, 2, 3):
            channel.write(_digest(ref, fill=ref))
        time.sleep(SETTLE_S)
        assert [ref for ref, _ in node.retained_digests] == [1, 2, 3]
        assert node.override_active is False

    def test_retained_digests_carry_the_bytes(self, node, channel):
        channel.write(_digest(1, fill=0x5A))
        time.sleep(SETTLE_S)
        assert node.retained_digests[0][1] == bytes([0x5A]) * 32

    def test_gap_raises_an_override(self, node, channel):
        channel.write(_digest(1))
        time.sleep(SETTLE_S)
        channel.write(_digest(5))
        time.sleep(SETTLE_S)
        assert node.override_active
        assert node.override_reason is OverrideReason.ATTESTATION_MISMATCH
        assert node.annunciator == ANNUNCIATOR_ATTEST_ALERT
        assert node.stats.chain_faults == 1

    def test_gap_is_acked_as_gap(self, node, channel):
        channel.write(_digest(1))
        time.sleep(SETTLE_S)
        _read_messages(channel, timeout_s=0.1)
        channel.write(_digest(5))
        msgs = _read_messages(channel, timeout_s=0.3)
        acks = [m for m in msgs if isinstance(m, AttestAck)]
        assert acks[0].verdict is AttestVerdict.GAP

    def test_rollback_raises_chain_break(self, node, channel):
        for ref in (1, 2, 3):
            channel.write(_digest(ref))
        time.sleep(SETTLE_S)
        _read_messages(channel, timeout_s=0.1)
        channel.write(_digest(2))  # replay of a reference already witnessed
        msgs = _read_messages(channel, timeout_s=0.3)
        acks = [m for m in msgs if isinstance(m, AttestAck)]
        assert acks[0].verdict is AttestVerdict.CHAIN_BREAK
        assert node.override_active

    def test_faulty_digest_is_not_retained(self, node, channel):
        channel.write(_digest(1))
        time.sleep(SETTLE_S)
        channel.write(_digest(9))
        time.sleep(SETTLE_S)
        assert [ref for ref, _ in node.retained_digests] == [1]

    def test_ring_evicts_oldest(self):
        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0, digest_capacity=4) as node:
            ch = open(node.device, "rb+", buffering=0)
            try:
                for ref in range(1, 8):
                    ch.write(_digest(ref, fill=ref))
                time.sleep(0.3)
                assert [ref for ref, _ in node.retained_digests] == [4, 5, 6, 7]
            finally:
                ch.close()


# ---------------------------------------------------------------------------
# Latch precedence
# ---------------------------------------------------------------------------

class TestLatchPrecedence:
    def test_first_reason_wins(self, node, channel):
        """A later trigger must not relabel what actually stopped the rig."""
        node.press_button()
        channel.write(_digest(4))  # a gap, which would also raise an override
        time.sleep(SETTLE_S)
        assert node.override_reason is OverrideReason.OPERATOR_BUTTON
        assert node.stats.overrides_asserted == 1

    def test_stats_snapshot_is_a_copy(self, node):
        before = node.stats
        node.press_button()
        assert before.overrides_asserted == 0
        assert node.stats.overrides_asserted == 1
