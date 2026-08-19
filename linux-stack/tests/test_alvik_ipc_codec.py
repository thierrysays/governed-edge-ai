"""
Tests for alvik-firmware/ipc_codec.py.

Runs the MicroPython-compatible codec under CPython. The codec contains no
MicroPython-specific APIs (struct, bytes -- all standard), so it can be
imported directly in the test environment via sys.path manipulation.

Tests cover:
  - CRC-16/CCITT computation
  - CommandRequest encoding (using the linux-stack codec) and decoding
  - CommandAck / CommandReject encoding
  - FrameParser incremental and split-buffer delivery
  - Governance gates: audit_ref == 0 is never decoded differently from valid
    (the gate lives in main.py, not in the codec; codec is purely structural)
"""

import os
import struct
import sys

import pytest

# Allow importing alvik-firmware codec from the repo root
_FIRMWARE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "alvik-firmware")
sys.path.insert(0, os.path.abspath(_FIRMWARE_DIR))

import ipc_codec as ac  # noqa: E402: sys.path must be set first

from ipc.codec import (  # noqa: E402
    ActionType,
    Actor,
    CommandRequest,
    encode,
)


def _cmd_request_frame(
    audit_ref: int = 42,
    action_type: ActionType = ActionType.MOVE_FORWARD,
    confidence: float = 0.91,
    action_param: int = 50,
) -> bytes:
    return encode(CommandRequest(
        audit_ref=audit_ref,
        timestamp_us=12345,
        actor=Actor.AI,
        confidence=confidence,
        action_type=action_type,
        action_param=action_param,
    ))


# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------

class TestCRC:
    def test_known_crc(self):
        data = b"\xa5\x01\x14\x00"
        crc = ac._crc16(data)
        assert isinstance(crc, int)
        assert 0 <= crc <= 0xFFFF

    def test_crc_differs_for_different_data(self):
        assert ac._crc16(b"\x00") != ac._crc16(b"\x01")

    def test_crc_empty_is_init(self):
        assert ac._crc16(b"") == 0xFFFF


# ---------------------------------------------------------------------------
# FrameParser
# ---------------------------------------------------------------------------

class TestFrameParser:
    def test_parse_command_request(self):
        parser = ac.FrameParser()
        frame = _cmd_request_frame(audit_ref=99, confidence=0.85)
        parser.feed(frame)
        msgs = parser.pop_messages()
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg.audit_ref == 99
        assert abs(msg.confidence - 0.85) < 1e-5
        assert msg.action_type == ac.ACTION_MOVE_FORWARD

    def test_parse_split_delivery(self):
        parser = ac.FrameParser()
        frame = _cmd_request_frame(audit_ref=7)
        mid = len(frame) // 2
        parser.feed(frame[:mid])
        assert parser.pop_messages() == []
        parser.feed(frame[mid:])
        msgs = parser.pop_messages()
        assert len(msgs) == 1
        assert msgs[0].audit_ref == 7

    def test_parse_two_consecutive_frames(self):
        parser = ac.FrameParser()
        f1 = _cmd_request_frame(audit_ref=1, action_param=50)
        f2 = _cmd_request_frame(audit_ref=2, action_param=30)
        parser.feed(f1 + f2)
        msgs = parser.pop_messages()
        assert len(msgs) == 2
        assert {m.audit_ref for m in msgs} == {1, 2}

    def test_corrupted_crc_discarded(self):
        parser = ac.FrameParser()
        frame = bytearray(_cmd_request_frame(audit_ref=5))
        frame[-1] ^= 0xFF  # flip last byte of CRC
        parser.feed(bytes(frame))
        assert parser.pop_messages() == []

    def test_non_magic_byte_skipped(self):
        parser = ac.FrameParser()
        frame = _cmd_request_frame(audit_ref=3)
        parser.feed(b"\x00\xFF\xDE" + frame)  # garbage prefix
        msgs = parser.pop_messages()
        assert len(msgs) == 1
        assert msgs[0].audit_ref == 3

    def test_pop_clears_queue(self):
        parser = ac.FrameParser()
        frame = _cmd_request_frame(audit_ref=10)
        parser.feed(frame)
        _ = parser.pop_messages()
        assert parser.pop_messages() == []

    def test_non_command_request_ignored(self):
        # Heartbeat frame (type 0x10, empty payload)
        magic = 0xA5
        msg_type = 0x10
        payload = b""
        header = struct.pack("<BBH", magic, msg_type, len(payload))
        body = header + payload
        crc = ac._crc16(body)
        frame = body + struct.pack("<H", crc)

        parser = ac.FrameParser()
        parser.feed(frame)
        assert parser.pop_messages() == []


# ---------------------------------------------------------------------------
# Encode ACK / Reject
# ---------------------------------------------------------------------------

class TestEncodeResponses:
    def test_encode_ack_is_valid_frame(self):
        frame = ac.encode_ack(audit_ref=42)
        assert frame[0] == 0xA5
        assert frame[1] == ac.MSG_COMMAND_ACK

    def test_encode_reject_is_valid_frame(self):
        frame = ac.encode_reject(audit_ref=42, reason=ac.REJ_AUDIT_REF_ZERO)
        assert frame[0] == 0xA5
        assert frame[1] == ac.MSG_COMMAND_REJECT

    def test_encode_ack_preserves_audit_ref(self):
        frame = ac.encode_ack(audit_ref=12345)
        # payload starts at byte 4; first 8 bytes are audit_ref uint64 LE
        ref = struct.unpack_from("<Q", frame, 4)[0]
        assert ref == 12345

    def test_encode_reject_preserves_reason(self):
        frame = ac.encode_reject(audit_ref=1, reason=ac.REJ_CONFIDENCE_BELOW_THRESHOLD)
        reason_byte = frame[4 + 8]  # byte after uint64 audit_ref
        assert reason_byte == ac.REJ_CONFIDENCE_BELOW_THRESHOLD

    def test_ack_crc_valid(self):
        frame = ac.encode_ack(audit_ref=1)
        body = frame[:-2]
        crc_in_frame = struct.unpack_from("<H", frame, len(frame) - 2)[0]
        assert crc_in_frame == ac._crc16(body)

    def test_reject_crc_valid(self):
        frame = ac.encode_reject(audit_ref=1, reason=ac.REJ_UNKNOWN_ACTION)
        body = frame[:-2]
        crc_in_frame = struct.unpack_from("<H", frame, len(frame) - 2)[0]
        assert crc_in_frame == ac._crc16(body)


# ---------------------------------------------------------------------------
# Governance gate constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_confidence_threshold(self):
        assert pytest.approx(0.70, abs=1e-9) == ac.CONFIDENCE_THRESHOLD

    def test_audit_ref_zero_reject_code(self):
        assert ac.REJ_AUDIT_REF_ZERO == 0x09

    def test_action_codes_match_linux_codec(self):
        assert int(ActionType.HALT)         == ac.ACTION_HALT
        assert int(ActionType.MOVE_FORWARD) == ac.ACTION_MOVE_FORWARD
        assert int(ActionType.MOVE_BACKWARD) == ac.ACTION_MOVE_BACKWARD
        assert int(ActionType.TURN_LEFT)    == ac.ACTION_TURN_LEFT
        assert int(ActionType.TURN_RIGHT)   == ac.ACTION_TURN_RIGHT
