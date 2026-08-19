"""
Smoke tests for linux-stack/ipc/mock_peer.py.

Each test exercises one complete interaction end-to-end over a real pty pair.
These run first in CI (pytest -m smoke) and should complete in under 2 seconds.
"""

import os

import pytest

from ipc.codec import (
    AckStatus,
    CommandAck,
    CommandReject,
    HaltNotify,
    HaltTrigger,
    Heartbeat,
    HeartbeatAck,
    RejectReason,
    StatusQuery,
    StatusResponse,
    SystemState,
)
from ipc.mock_peer import MockSTM32H5
from tests.test_mock_peer import _cmd, _read_one, _send


@pytest.mark.smoke
def test_import():
    """MockSTM32H5 is importable and constructible."""
    from ipc.mock_peer import MockSTM32H5  # noqa: F401


@pytest.mark.smoke
def test_heartbeat_roundtrip():
    """Heartbeat → HeartbeatAck is the fundamental liveness path."""
    with MockSTM32H5(watchdog_ms=2000) as peer:
        fd = os.open(peer.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            _send(fd, Heartbeat())
            resp = _read_one(fd)
            assert isinstance(resp, HeartbeatAck)
        finally:
            os.close(fd)


@pytest.mark.smoke
def test_log_before_act_enforced():
    """COMMAND_REQUEST with audit_ref=0 is rejected: log-before-act invariant."""
    with MockSTM32H5(watchdog_ms=2000) as peer:
        fd = os.open(peer.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            _send(fd, _cmd(audit_ref=0))
            resp = _read_one(fd)
            assert isinstance(resp, CommandReject)
            assert resp.reason == RejectReason.AUDIT_REF_ZERO
        finally:
            os.close(fd)


@pytest.mark.smoke
def test_governance_command_lifecycle():
    """
    Full governance lifecycle:
    Heartbeat → HeartbeatAck → COMMAND_REQUEST → COMMAND_ACK → STATUS_RESPONSE.
    Exercises the core execution path the Linux AI stack uses.
    """
    with MockSTM32H5(watchdog_ms=2000) as peer:
        fd = os.open(peer.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            # 1. Establish liveness
            _send(fd, Heartbeat())
            assert isinstance(_read_one(fd), HeartbeatAck)

            # 2. Send governed command (audit_ref=7 simulates a confirmed log row)
            _send(fd, _cmd(audit_ref=7, confidence=0.92))
            ack = _read_one(fd)
            assert isinstance(ack, CommandAck)
            assert ack.audit_ref == 7
            assert ack.status == AckStatus.EXECUTING

            # 3. Verify stats reflect the execution
            _send(fd, StatusQuery())
            status = _read_one(fd)
            assert isinstance(status, StatusResponse)
            assert status.commands_received == 1
            assert status.commands_executed == 1
            assert status.commands_rejected == 0
            assert status.system_state == SystemState.ARMED
        finally:
            os.close(fd)


@pytest.mark.smoke
def test_kill_switch_halts_system():
    """
    Kill switch path: trigger → HALT_NOTIFY → subsequent commands rejected.
    The STM32H5 is sole execution authority; once halted, it refuses all commands.
    """
    with MockSTM32H5(watchdog_ms=2000) as peer:
        fd = os.open(peer.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            peer.trigger_kill_switch()
            notify = _read_one(fd)
            assert isinstance(notify, HaltNotify)
            assert notify.trigger == HaltTrigger.KILL_SWITCH_GPIO

            _send(fd, _cmd(audit_ref=99))
            reject = _read_one(fd)
            assert isinstance(reject, CommandReject)
            assert reject.reason == RejectReason.KILL_SWITCH_ACTIVE
        finally:
            os.close(fd)
