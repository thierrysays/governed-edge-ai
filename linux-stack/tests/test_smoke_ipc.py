"""
Smoke tests for the IPC codec.
Run with: pytest -m smoke
Exercises the full log-before-act → encode → transmit → decode → ack lifecycle
without any hardware.
"""

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
    crc16_ccitt,
    decode,
    encode,
)


@pytest.mark.smoke
def test_crc_known_vector():
    assert crc16_ccitt(b"123456789") == 0x29B1


@pytest.mark.smoke
def test_all_message_types_encodable():
    messages = [
        Heartbeat(),
        HeartbeatAck(),
        StatusQuery(),
        CommandRequest(audit_ref=1, timestamp_us=0, actor=Actor.AI,
                       confidence=0.9, action_type=ActionType.NONE, action_param=0),
        CommandAck(audit_ref=1, status=AckStatus.QUEUED),
        CommandReject(audit_ref=1, reason=RejectReason.KILL_SWITCH_ACTIVE),
        HaltNotify(timestamp_us=0, trigger=HaltTrigger.WATCHDOG),
        StatusResponse(system_state=SystemState.ARMED, kill_switch_gpio=0,
                       commands_received=0, commands_rejected=0, commands_executed=0),
    ]
    for msg in messages:
        frame = encode(msg)
        assert frame[0] == 0xA5, f"Bad magic for {type(msg).__name__}"
        decoded = decode(frame)
        if isinstance(msg, CommandRequest):
            # confidence is stored as float32; exact equality fails for most decimals
            assert decoded.audit_ref == msg.audit_ref
            assert decoded.actor == msg.actor
            assert decoded.confidence == pytest.approx(msg.confidence, abs=1e-6)
            assert decoded.action_type == msg.action_type
        else:
            assert decoded == msg


@pytest.mark.smoke
def test_log_before_act_protocol_sequence():
    """
    Simulate the governance-critical sequence:
      1. audit_ref obtained from logger (mocked as integer here)
      2. CommandRequest built with audit_ref != 0
      3. Frame encoded and decoded by peer
      4. COMMAND_ACK echoes audit_ref
      5. audit_ref from ACK matches the original
    """
    audit_ref = 17  # would be returned by AuditLogger.log_event()
    assert audit_ref != 0, "log-before-act: audit_ref must be non-zero"

    req = CommandRequest(
        audit_ref=audit_ref,
        timestamp_us=2_500_000,
        actor=Actor.AI,
        confidence=0.88,
        action_type=ActionType.HALT,
        action_param=0,
    )
    frame = encode(req)

    # Peer (STM32H5 side) decodes and echoes audit_ref in ACK
    decoded_req = decode(frame)
    ack = CommandAck(audit_ref=decoded_req.audit_ref, status=AckStatus.QUEUED)
    ack_frame = encode(ack)

    # Linux side decodes ACK and verifies echo
    decoded_ack = decode(ack_frame)
    assert decoded_ack.audit_ref == audit_ref


@pytest.mark.smoke
def test_watchdog_halt_flow():
    """STM32H5 sends HALT_NOTIFY without any request: Linux side must handle it."""
    halt = HaltNotify(timestamp_us=999_999, trigger=HaltTrigger.WATCHDOG)
    frame = encode(halt)

    parser = FrameParser()
    parser.feed(frame)
    msgs = parser.pop_messages()

    assert len(msgs) == 1
    msg = msgs[0]
    assert isinstance(msg, HaltNotify)
    assert msg.trigger == HaltTrigger.WATCHDOG


@pytest.mark.smoke
def test_heartbeat_round_trip():
    """Linux sends HEARTBEAT, STM32H5 responds HEARTBEAT_ACK."""
    hb_frame = encode(Heartbeat())
    hb_ack_frame = encode(HeartbeatAck())

    assert decode(hb_frame) == Heartbeat()
    assert decode(hb_ack_frame) == HeartbeatAck()
