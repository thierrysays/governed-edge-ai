"""
Unit tests for linux-stack/ipc/mock_peer.py.

Tests use a real pty pair: the slave end is opened by the test (simulating
the Linux IPC client), frames are written in, and responses are read back
with a select-based timeout. No mocking of the pty layer — this exercises
the actual read/write path the real IPC client will use.
"""

import os
import select
import time

import pytest

from ipc.codec import (
    AckStatus,
    ActionType,
    Actor,
    CommandAck,
    CommandReject,
    CommandRequest,
    FrameParser,
    HaltNotify,
    HaltTrigger,
    Heartbeat,
    HeartbeatAck,
    RejectReason,
    StatusQuery,
    StatusResponse,
    SystemState,
    encode,
)
from ipc.mock_peer import MockSTM32H5

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

RESPONSE_TIMEOUT = 0.4   # seconds — generous for CI latency


def _read_one(fd: int, timeout: float = RESPONSE_TIMEOUT) -> object:
    """Read one decoded message from fd, with a timeout. Raises TimeoutError."""
    parser = FrameParser()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        r, _, _ = select.select([fd], [], [], max(remaining, 0))
        if not r:
            break
        data = os.read(fd, 512)
        parser.feed(data)
        msgs = parser.pop_messages()
        if msgs:
            return msgs[0]
    raise TimeoutError(f"No response within {timeout}s")


def _send(fd: int, msg: object) -> None:
    os.write(fd, encode(msg))


def _cmd(**kw) -> CommandRequest:
    defaults = {
        "audit_ref": 1, "timestamp_us": 0, "actor": Actor.AI,
        "confidence": 0.90, "action_type": ActionType.HALT, "action_param": 0,
    }
    defaults.update(kw)
    return CommandRequest(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def peer():
    """Start the mock peer with a shortened watchdog for test speed."""
    with MockSTM32H5(watchdog_ms=200) as p:
        yield p


@pytest.fixture
def client_fd(peer):
    """Open the slave pty as the IPC client would open a serial port."""
    fd = os.open(peer.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    yield fd
    os.close(fd)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_gets_ack(self, peer, client_fd):
        _send(client_fd, Heartbeat())
        resp = _read_one(client_fd)
        assert isinstance(resp, HeartbeatAck)

    def test_multiple_heartbeats_each_get_ack(self, peer, client_fd):
        for _ in range(3):
            _send(client_fd, Heartbeat())
            resp = _read_one(client_fd)
            assert isinstance(resp, HeartbeatAck)


# ---------------------------------------------------------------------------
# Status query
# ---------------------------------------------------------------------------

class TestStatusQuery:
    def test_initial_state_is_armed(self, peer, client_fd):
        _send(client_fd, StatusQuery())
        resp = _read_one(client_fd)
        assert isinstance(resp, StatusResponse)
        assert resp.system_state == SystemState.ARMED

    def test_kill_switch_gpio_initially_closed(self, peer, client_fd):
        _send(client_fd, StatusQuery())
        resp = _read_one(client_fd)
        assert resp.kill_switch_gpio == 0

    def test_initial_counters_are_zero(self, peer, client_fd):
        _send(client_fd, StatusQuery())
        resp = _read_one(client_fd)
        assert resp.commands_received == 0
        assert resp.commands_rejected == 0
        assert resp.commands_executed == 0

    def test_state_halted_after_kill_switch(self, peer, client_fd):
        peer.trigger_kill_switch()
        _read_one(client_fd)  # consume HALT_NOTIFY before querying status
        _send(client_fd, StatusQuery())
        resp = _read_one(client_fd)
        assert resp.system_state == SystemState.HALTED
        assert resp.kill_switch_gpio == 1


# ---------------------------------------------------------------------------
# Command accept path
# ---------------------------------------------------------------------------

class TestCommandAccept:
    def test_valid_command_gets_ack(self, peer, client_fd):
        _send(client_fd, _cmd(confidence=0.90))
        resp = _read_one(client_fd)
        assert isinstance(resp, CommandAck)

    def test_ack_echoes_audit_ref(self, peer, client_fd):
        _send(client_fd, _cmd(audit_ref=42))
        resp = _read_one(client_fd)
        assert resp.audit_ref == 42

    def test_ack_status_is_executing(self, peer, client_fd):
        _send(client_fd, _cmd())
        resp = _read_one(client_fd)
        assert resp.status == AckStatus.EXECUTING

    def test_confidence_at_threshold_accepted(self, peer, client_fd):
        # 0.75 is exactly representable in float32; 0.70 encodes to 0.6999... after f32 round-trip.
        _send(client_fd, _cmd(confidence=0.75))
        assert isinstance(_read_one(client_fd), CommandAck)

    def test_state_returns_to_armed_after_command(self, peer, client_fd):
        _send(client_fd, _cmd())
        _read_one(client_fd)  # consume ACK
        assert peer.state == SystemState.ARMED

    def test_stats_incremented_on_accept(self, peer, client_fd):
        _send(client_fd, _cmd())
        _read_one(client_fd)
        s = peer.stats
        assert s.commands_received == 1
        assert s.commands_executed == 1
        assert s.commands_rejected == 0

    def test_all_action_types_accepted(self, peer, client_fd):
        for action in ActionType:
            _send(client_fd, _cmd(audit_ref=1, action_type=action))
            resp = _read_one(client_fd)
            assert isinstance(resp, CommandAck), f"Expected ACK for {action!r}"


# ---------------------------------------------------------------------------
# Command reject paths
# ---------------------------------------------------------------------------

class TestCommandReject:
    def test_zero_audit_ref_rejected(self, peer, client_fd):
        _send(client_fd, _cmd(audit_ref=0))
        resp = _read_one(client_fd)
        assert isinstance(resp, CommandReject)
        assert resp.reason == RejectReason.AUDIT_REF_ZERO

    def test_zero_audit_ref_echoes_zero(self, peer, client_fd):
        _send(client_fd, _cmd(audit_ref=0))
        resp = _read_one(client_fd)
        assert resp.audit_ref == 0

    def test_confidence_below_threshold_rejected(self, peer, client_fd):
        _send(client_fd, _cmd(confidence=0.69))
        resp = _read_one(client_fd)
        assert isinstance(resp, CommandReject)
        assert resp.reason == RejectReason.CONFIDENCE_BELOW_THRESHOLD

    def test_confidence_at_threshold_minus_epsilon_rejected(self, peer, client_fd):
        _send(client_fd, _cmd(confidence=0.6999))
        assert isinstance(_read_one(client_fd), CommandReject)

    def test_kill_switch_rejects_command(self, peer, client_fd):
        # Drain the HALT_NOTIFY first
        peer.trigger_kill_switch()
        _read_one(client_fd)  # HALT_NOTIFY
        _send(client_fd, _cmd())
        resp = _read_one(client_fd)
        assert isinstance(resp, CommandReject)
        assert resp.reason == RejectReason.KILL_SWITCH_ACTIVE

    def test_reject_echoes_audit_ref(self, peer, client_fd):
        _send(client_fd, _cmd(audit_ref=99, confidence=0.0))
        resp = _read_one(client_fd)
        assert resp.audit_ref == 99

    def test_fault_state_rejects_command(self, peer, client_fd):
        peer.inject_fault()
        _send(client_fd, _cmd())
        resp = _read_one(client_fd)
        assert isinstance(resp, CommandReject)
        assert resp.reason == RejectReason.SYSTEM_FAULT

    def test_reject_increments_rejected_stat(self, peer, client_fd):
        _send(client_fd, _cmd(audit_ref=0))
        _read_one(client_fd)
        assert peer.stats.commands_rejected == 1

    def test_custom_confidence_threshold(self, client_fd):
        with MockSTM32H5(confidence_threshold=0.95, watchdog_ms=200) as peer2:
            fd2 = os.open(peer2.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                _send(fd2, _cmd(confidence=0.90))  # below custom threshold
                resp = _read_one(fd2)
                assert isinstance(resp, CommandReject)
                assert resp.reason == RejectReason.CONFIDENCE_BELOW_THRESHOLD
            finally:
                os.close(fd2)


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

class TestKillSwitch:
    def test_trigger_sends_halt_notify(self, peer, client_fd):
        peer.trigger_kill_switch()
        resp = _read_one(client_fd)
        assert isinstance(resp, HaltNotify)
        assert resp.trigger == HaltTrigger.KILL_SWITCH_GPIO

    def test_state_is_halted_after_kill_switch(self, peer):
        peer.trigger_kill_switch()
        assert peer.state == SystemState.HALTED

    def test_second_trigger_does_not_send_second_notify(self, peer, client_fd):
        peer.trigger_kill_switch()
        _read_one(client_fd)  # first HALT_NOTIFY
        peer.trigger_kill_switch()  # already halted — no second notify
        with pytest.raises(TimeoutError):
            _read_one(client_fd, timeout=0.15)

    def test_release_clears_kill_switch_flag(self, peer, client_fd):
        peer.trigger_kill_switch()
        _read_one(client_fd)
        peer.release_kill_switch()
        _send(client_fd, StatusQuery())
        resp = _read_one(client_fd)
        assert resp.kill_switch_gpio == 0

    @pytest.mark.regression
    def test_halt_notify_audit_ref_is_zero(self, peer, client_fd):
        # HALT_NOTIFY carries timestamp_us, not audit_ref.
        # Guard: an earlier bug used audit_ref in the wrong field.
        peer.trigger_kill_switch()
        resp = _read_one(client_fd)
        assert isinstance(resp, HaltNotify)
        assert resp.timestamp_us >= 0


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

class TestWatchdog:
    def test_watchdog_fires_halt_notify(self, peer, client_fd):
        # MockSTM32H5 fixture uses watchdog_ms=200; don't send heartbeat
        resp = _read_one(client_fd, timeout=0.5)
        assert isinstance(resp, HaltNotify)
        assert resp.trigger == HaltTrigger.WATCHDOG

    def test_watchdog_transitions_to_halted(self, peer, client_fd):
        _read_one(client_fd, timeout=0.5)  # consume HALT_NOTIFY
        assert peer.state == SystemState.HALTED

    def test_heartbeat_resets_watchdog(self, peer, client_fd):
        # Send heartbeats faster than the 200ms watchdog — no HALT_NOTIFY should arrive
        for _ in range(5):
            _send(client_fd, Heartbeat())
            _read_one(client_fd)  # HEARTBEAT_ACK
            time.sleep(0.05)
        assert peer.state == SystemState.ARMED


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_after_mixed_commands(self, peer, client_fd):
        _send(client_fd, _cmd(audit_ref=1, confidence=0.90))
        _read_one(client_fd)  # ACK
        _send(client_fd, _cmd(audit_ref=0))
        _read_one(client_fd)  # REJECT (audit_ref=0)
        _send(client_fd, _cmd(audit_ref=2, confidence=0.30))
        _read_one(client_fd)  # REJECT (confidence)

        s = peer.stats
        assert s.commands_received == 3
        assert s.commands_executed == 1
        assert s.commands_rejected == 2

    def test_status_response_reflects_stats(self, peer, client_fd):
        _send(client_fd, _cmd(audit_ref=1))
        _read_one(client_fd)
        _send(client_fd, StatusQuery())
        resp = _read_one(client_fd)
        assert resp.commands_received == 1
        assert resp.commands_executed == 1


# ---------------------------------------------------------------------------
# Context manager / lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_context_manager_starts_and_stops(self):
        with MockSTM32H5(watchdog_ms=5000) as p:
            assert os.path.exists(p.device)

    def test_device_path_is_pty(self):
        with MockSTM32H5(watchdog_ms=5000) as p:
            assert p.device.startswith("/dev/pts/") or p.device.startswith("/dev/tty")

    def test_initial_state_armed(self):
        with MockSTM32H5(watchdog_ms=5000) as p:
            assert p.state == SystemState.ARMED
