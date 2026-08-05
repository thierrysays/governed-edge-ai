"""
Unit tests for linux-stack/ipc/codec.py.

Covers: CRC correctness, encode/decode round-trips for all 8 message types,
frame byte structure, error cases, and the incremental FrameParser.
"""

import struct

import pytest

from ipc.codec import (
    MAGIC,
    AckStatus,
    ActionType,
    Actor,
    CommandAck,
    CommandReject,
    CommandRequest,
    FrameError,
    FrameParser,
    HaltNotify,
    HaltTrigger,
    Heartbeat,
    HeartbeatAck,
    MsgType,
    RejectReason,
    StatusQuery,
    StatusResponse,
    SystemState,
    crc16_ccitt,
    decode,
    encode,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cmd_req(**kw) -> CommandRequest:
    defaults = {
        "audit_ref": 42, "timestamp_us": 1_000_000,
        "actor": Actor.AI, "confidence": 0.91,
        "action_type": ActionType.HALT, "action_param": 0,
    }
    defaults.update(kw)
    return CommandRequest(**defaults)


# ---------------------------------------------------------------------------
# CRC-16/CCITT
# ---------------------------------------------------------------------------

class TestCRC:
    def test_known_vector(self):
        # Standard CCITT / IBM-3740 test vector
        assert crc16_ccitt(b"123456789") == 0x29B1

    def test_empty_input(self):
        # Defined behaviour: CRC of empty = init value 0xFFFF
        assert crc16_ccitt(b"") == 0xFFFF

    def test_single_zero_byte(self):
        result = crc16_ccitt(b"\x00")
        assert isinstance(result, int)
        assert 0 <= result <= 0xFFFF

    def test_different_data_different_crc(self):
        assert crc16_ccitt(b"\x01") != crc16_ccitt(b"\x02")

    def test_returns_16_bit_value(self):
        assert crc16_ccitt(b"\xFF" * 100) <= 0xFFFF


# ---------------------------------------------------------------------------
# Frame structure
# ---------------------------------------------------------------------------

class TestFrameStructure:
    def test_heartbeat_is_6_bytes(self):
        assert len(encode(Heartbeat())) == 6  # 4 header + 0 payload + 2 CRC

    def test_heartbeat_magic(self):
        frame = encode(Heartbeat())
        assert frame[0] == MAGIC

    def test_heartbeat_type_byte(self):
        frame = encode(Heartbeat())
        assert frame[1] == MsgType.HEARTBEAT

    def test_heartbeat_payload_len_is_zero(self):
        frame = encode(Heartbeat())
        assert frame[2:4] == b"\x00\x00"

    def test_command_request_is_26_bytes(self):
        assert len(encode(_cmd_req())) == 26  # 4 + 20 + 2

    def test_command_ack_is_15_bytes(self):
        msg = CommandAck(audit_ref=1, status=AckStatus.QUEUED)
        assert len(encode(msg)) == 15  # 4 + 9 + 2

    def test_status_response_is_20_bytes(self):
        msg = StatusResponse(
            system_state=SystemState.ARMED, kill_switch_gpio=0,
            commands_received=0, commands_rejected=0, commands_executed=0,
        )
        assert len(encode(msg)) == 20  # 4 + 14 + 2

    def test_crc_is_last_two_bytes_little_endian(self):
        frame = encode(Heartbeat())
        body = frame[:-2]
        expected_crc = crc16_ccitt(body)
        actual_crc = struct.unpack("<H", frame[-2:])[0]
        assert actual_crc == expected_crc


# ---------------------------------------------------------------------------
# Round-trip: encode → decode for all 8 message types
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_heartbeat(self):
        assert decode(encode(Heartbeat())) == Heartbeat()

    def test_heartbeat_ack(self):
        assert decode(encode(HeartbeatAck())) == HeartbeatAck()

    def test_status_query(self):
        assert decode(encode(StatusQuery())) == StatusQuery()

    def test_command_request_fields(self):
        msg = _cmd_req(audit_ref=999, timestamp_us=5_000_000,
                       actor=Actor.HUMAN_OVERRIDE, confidence=0.75,
                       action_type=ActionType.MOVE_JOINT_3, action_param=-450)
        decoded = decode(encode(msg))
        assert decoded.audit_ref == 999
        assert decoded.timestamp_us == 5_000_000
        assert decoded.actor == Actor.HUMAN_OVERRIDE
        assert abs(decoded.confidence - 0.75) < 1e-6  # float32 precision
        assert decoded.action_type == ActionType.MOVE_JOINT_3
        assert decoded.action_param == -450

    def test_command_request_all_action_types(self):
        for action in ActionType:
            msg = _cmd_req(action_type=action)
            assert decode(encode(msg)).action_type == action

    def test_command_ack_queued(self):
        msg = CommandAck(audit_ref=7, status=AckStatus.QUEUED)
        decoded = decode(encode(msg))
        assert decoded.audit_ref == 7
        assert decoded.status == AckStatus.QUEUED

    def test_command_ack_executing(self):
        msg = CommandAck(audit_ref=7, status=AckStatus.EXECUTING)
        assert decode(encode(msg)).status == AckStatus.EXECUTING

    def test_command_reject_all_reasons(self):
        for reason in RejectReason:
            msg = CommandReject(audit_ref=0, reason=reason)
            assert decode(encode(msg)).reason == reason

    def test_halt_notify_all_triggers(self):
        for trigger in HaltTrigger:
            msg = HaltNotify(timestamp_us=12345, trigger=trigger)
            decoded = decode(encode(msg))
            assert decoded.trigger == trigger
            assert decoded.timestamp_us == 12345

    def test_status_response_all_states(self):
        for state in SystemState:
            msg = StatusResponse(
                system_state=state, kill_switch_gpio=0,
                commands_received=100, commands_rejected=5, commands_executed=95,
            )
            decoded = decode(encode(msg))
            assert decoded.system_state == state

    def test_status_response_counters(self):
        msg = StatusResponse(
            system_state=SystemState.ARMED, kill_switch_gpio=1,
            commands_received=2**32 - 1, commands_rejected=0, commands_executed=2**32 - 1,
        )
        decoded = decode(encode(msg))
        assert decoded.commands_received == 2**32 - 1
        assert decoded.kill_switch_gpio == 1

    def test_audit_ref_max_uint64(self):
        msg = _cmd_req(audit_ref=2**64 - 1)
        assert decode(encode(msg)).audit_ref == 2**64 - 1

    def test_negative_action_param(self):
        msg = _cmd_req(action_param=-32768)  # int16 min
        assert decode(encode(msg)).action_param == -32768

    def test_positive_action_param(self):
        msg = _cmd_req(action_param=32767)  # int16 max
        assert decode(encode(msg)).action_param == 32767

    def test_confidence_zero(self):
        msg = _cmd_req(confidence=0.0)
        assert decode(encode(msg)).confidence == pytest.approx(0.0, abs=1e-7)

    def test_confidence_one(self):
        msg = _cmd_req(confidence=1.0)
        assert decode(encode(msg)).confidence == pytest.approx(1.0, abs=1e-7)

    def test_msg_type_field_preserved(self):
        for cls, args in [
            (Heartbeat, {}),
            (HeartbeatAck, {}),
            (StatusQuery, {}),
            (CommandAck, {"audit_ref": 1, "status": AckStatus.QUEUED}),
        ]:
            msg = cls(**args)
            assert decode(encode(msg)).msg_type == msg.msg_type


# ---------------------------------------------------------------------------
# Decode error cases
# ---------------------------------------------------------------------------

class TestDecodeErrors:
    def test_too_short_raises(self):
        with pytest.raises(FrameError, match="too short"):
            decode(b"\xA5\x10\x00")

    def test_bad_magic_raises(self):
        frame = bytearray(encode(Heartbeat()))
        frame[0] = 0xFF
        with pytest.raises(FrameError, match="magic"):
            decode(bytes(frame))

    def test_crc_corruption_raises(self):
        frame = bytearray(encode(Heartbeat()))
        frame[-1] ^= 0xFF  # flip last CRC byte
        with pytest.raises(FrameError, match="CRC"):
            decode(bytes(frame))

    def test_payload_byte_corruption_raises(self):
        frame = bytearray(encode(_cmd_req()))
        frame[5] ^= 0xFF  # corrupt inside payload
        with pytest.raises(FrameError, match="CRC"):
            decode(bytes(frame))

    def test_type_byte_corruption_raises(self):
        frame = bytearray(encode(Heartbeat()))
        frame[1] = 0xFF  # unknown type byte
        # CRC needs recomputing so the frame looks structurally valid
        crc = crc16_ccitt(bytes(frame[:-2]))
        frame[-2:] = struct.pack("<H", crc)
        with pytest.raises(FrameError, match="Unknown message type"):
            decode(bytes(frame))

    def test_truncated_frame_raises(self):
        frame = encode(_cmd_req())
        with pytest.raises(FrameError):
            decode(frame[:-1])

    def test_extended_frame_raises(self):
        frame = encode(Heartbeat()) + b"\x00"
        with pytest.raises(FrameError):
            decode(frame)

    def test_empty_input_raises(self):
        with pytest.raises(FrameError):
            decode(b"")


# ---------------------------------------------------------------------------
# FrameParser (incremental stream input)
# ---------------------------------------------------------------------------

class TestFrameParser:
    def test_single_complete_frame(self):
        parser = FrameParser()
        parser.feed(encode(Heartbeat()))
        assert len(parser.pop_messages()) == 1

    def test_pop_drains(self):
        parser = FrameParser()
        parser.feed(encode(Heartbeat()))
        parser.pop_messages()
        assert parser.pop_messages() == []

    def test_two_frames_in_one_feed(self):
        parser = FrameParser()
        parser.feed(encode(Heartbeat()) + encode(HeartbeatAck()))
        msgs = parser.pop_messages()
        assert len(msgs) == 2
        assert isinstance(msgs[0], Heartbeat)
        assert isinstance(msgs[1], HeartbeatAck)

    def test_fragmented_frame_byte_by_byte(self):
        frame = encode(_cmd_req())
        parser = FrameParser()
        for byte in frame[:-1]:
            parser.feed(bytes([byte]))
            assert parser.pop_messages() == []
        parser.feed(frame[-1:])
        msgs = parser.pop_messages()
        assert len(msgs) == 1
        assert isinstance(msgs[0], CommandRequest)

    def test_garbage_prefix_discarded(self):
        garbage = bytes([0x00, 0xFF, 0x42, 0x13])
        parser = FrameParser()
        parser.feed(garbage + encode(Heartbeat()))
        msgs = parser.pop_messages()
        assert len(msgs) == 1
        assert isinstance(msgs[0], Heartbeat)

    def test_garbage_only_clears_buffer(self):
        parser = FrameParser()
        parser.feed(bytes([0x01, 0x02, 0x03]))
        assert parser.pop_messages() == []

    def test_fragmented_header_then_rest(self):
        frame = encode(Heartbeat())
        parser = FrameParser()
        parser.feed(frame[:2])
        assert parser.pop_messages() == []
        parser.feed(frame[2:])
        assert len(parser.pop_messages()) == 1

    def test_corrupt_frame_logged_to_errors(self):
        frame = bytearray(encode(Heartbeat()))
        frame[-1] ^= 0xFF  # break CRC
        parser = FrameParser()
        # Wrap with a valid frame after so the parser keeps going
        parser.feed(bytes(frame) + encode(HeartbeatAck()))
        parser.pop_messages()
        assert len(parser.errors) >= 1
        assert "CRC" in parser.errors[0]

    def test_multiple_message_types_in_stream(self):
        stream = b"".join([
            encode(Heartbeat()),
            encode(CommandAck(audit_ref=1, status=AckStatus.QUEUED)),
            encode(StatusQuery()),
            encode(HaltNotify(timestamp_us=500, trigger=HaltTrigger.KILL_SWITCH_GPIO)),
        ])
        parser = FrameParser()
        parser.feed(stream)
        msgs = parser.pop_messages()
        assert len(msgs) == 4
        assert isinstance(msgs[0], Heartbeat)
        assert isinstance(msgs[1], CommandAck)
        assert isinstance(msgs[2], StatusQuery)
        assert isinstance(msgs[3], HaltNotify)
