"""
Unit tests for the five oversight message types added to the IPC codec.

Covers the VENTUNO Q <-> UNO R4 WiFi subset: round-trips, wire sizes, CRC
protection, payload-length guards and enum validation at decode time.
"""

import struct

import pytest

from ipc.codec import (
    MAGIC,
    ActionType,
    Actor,
    AttestAck,
    AttestDigest,
    AttestVerdict,
    CommandRequest,
    FrameError,
    FrameParser,
    HaltTrigger,
    MsgType,
    OverrideAssert,
    OverrideClear,
    OverrideReason,
    RejectReason,
    SupervisorHeartbeat,
    SystemState,
    decode,
    encode,
)


def _corrupt_payload(frame: bytes) -> bytes:
    body = bytearray(frame)
    body[5] ^= 0xFF
    return bytes(body)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TestOversightEnums:
    def test_new_message_types_do_not_collide(self):
        values = [int(m) for m in MsgType]
        assert len(values) == len(set(values))

    def test_oversight_message_type_values(self):
        assert MsgType.SUPERVISOR_HEARTBEAT == 0x30
        assert MsgType.ATTEST_DIGEST == 0x31
        assert MsgType.OVERRIDE_ASSERT == 0xA0
        assert MsgType.OVERRIDE_CLEAR == 0xA1
        assert MsgType.ATTEST_ACK == 0xA2

    def test_oversight_actor_added(self):
        assert Actor.OVERSIGHT == 0x03

    def test_oversight_reject_reason_added(self):
        assert RejectReason.OVERSIGHT_OVERRIDE_ACTIVE == 0x0A

    def test_supervisor_halt_trigger_added(self):
        assert HaltTrigger.SUPERVISOR_OVERRIDE == 0x05

    def test_override_reasons(self):
        assert OverrideReason.OPERATOR_BUTTON == 0x01
        assert OverrideReason.GOVERNANCE_HEARTBEAT_LOST == 0x02
        assert OverrideReason.ATTESTATION_MISMATCH == 0x03
        assert OverrideReason.REMOTE_CONSOLE == 0x04

    def test_attest_verdicts(self):
        assert AttestVerdict.CHAIN_OK == 0x00
        assert AttestVerdict.CHAIN_BREAK == 0x01
        assert AttestVerdict.GAP == 0x02


# ---------------------------------------------------------------------------
# Round-trips and wire sizes
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_supervisor_heartbeat(self):
        msg = SupervisorHeartbeat(
            last_audit_ref=4096, system_state=SystemState.ARMED,
            events_logged=120, commands_sent=17,
        )
        assert decode(encode(msg)) == msg

    def test_supervisor_heartbeat_wire_size(self):
        msg = SupervisorHeartbeat(1, SystemState.ARMED, 0, 0)
        assert len(encode(msg)) == 4 + 17 + 2

    def test_attest_digest(self):
        msg = AttestDigest(audit_ref=7, digest=bytes(range(32)))
        assert decode(encode(msg)) == msg

    def test_attest_digest_wire_size(self):
        msg = AttestDigest(1, b"\xaa" * 32)
        assert len(encode(msg)) == 4 + 40 + 2

    def test_override_assert(self):
        msg = OverrideAssert(
            timestamp_us=123_456_789, reason=OverrideReason.OPERATOR_BUTTON
        )
        assert decode(encode(msg)) == msg

    def test_override_clear(self):
        msg = OverrideClear(timestamp_us=987_654_321)
        assert decode(encode(msg)) == msg

    def test_attest_ack(self):
        msg = AttestAck(audit_ref=99, verdict=AttestVerdict.CHAIN_BREAK)
        assert decode(encode(msg)) == msg

    def test_uint64_audit_ref_survives(self):
        big = (1 << 63) + 12345
        assert decode(encode(AttestDigest(big, b"\x01" * 32))).audit_ref == big

    @pytest.mark.parametrize("reason", list(OverrideReason))
    def test_every_override_reason_round_trips(self, reason):
        assert decode(encode(OverrideAssert(1, reason))).reason is reason

    @pytest.mark.parametrize("verdict", list(AttestVerdict))
    def test_every_verdict_round_trips(self, verdict):
        assert decode(encode(AttestAck(1, verdict))).verdict is verdict


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_digest_must_be_32_bytes(self):
        with pytest.raises(ValueError, match="32 bytes"):
            AttestDigest(audit_ref=1, digest=b"\x00" * 31)

    def test_crc_protects_oversight_frames(self):
        frame = _corrupt_payload(encode(OverrideAssert(1, OverrideReason.OPERATOR_BUTTON)))
        with pytest.raises(FrameError, match="CRC mismatch"):
            decode(frame)

    def test_short_payload_rejected(self):
        # A SUPERVISOR_HEARTBEAT header claiming 9 bytes instead of 17.
        body = struct.pack("<BBH", MAGIC, int(MsgType.SUPERVISOR_HEARTBEAT), 9) + b"\x00" * 9
        from ipc.codec import crc16_ccitt
        frame = body + struct.pack("<H", crc16_ccitt(body))
        with pytest.raises(FrameError, match="expected 17 payload bytes"):
            decode(frame)

    def test_unknown_override_reason_rejected(self):
        good = bytearray(encode(OverrideAssert(1, OverrideReason.OPERATOR_BUTTON)))
        good[12] = 0x7F  # reason byte
        from ipc.codec import crc16_ccitt
        frame = bytes(good[:-2]) + struct.pack("<H", crc16_ccitt(bytes(good[:-2])))
        with pytest.raises(ValueError):
            decode(frame)


# ---------------------------------------------------------------------------
# Stream parsing: the oversight link shares the FrameParser
# ---------------------------------------------------------------------------

class TestStreamParsing:
    def test_mixed_stream_of_oversight_frames(self):
        msgs = [
            SupervisorHeartbeat(1, SystemState.ARMED, 1, 0),
            AttestDigest(1, b"\x02" * 32),
            AttestAck(1, AttestVerdict.CHAIN_OK),
            OverrideAssert(500, OverrideReason.ATTESTATION_MISMATCH),
            OverrideClear(600),
        ]
        parser = FrameParser()
        parser.feed(b"".join(encode(m) for m in msgs))
        assert parser.pop_messages() == msgs

    def test_split_across_reads(self):
        frame = encode(AttestDigest(9, b"\x03" * 32))
        parser = FrameParser()
        for i in range(0, len(frame), 7):
            parser.feed(frame[i:i + 7])
        assert parser.pop_messages() == [AttestDigest(9, b"\x03" * 32)]

    def test_actuation_and_oversight_frames_interleave(self):
        """Both links share one codec; a parser must not confuse the two."""
        req = CommandRequest(
            audit_ref=5, timestamp_us=10, actor=Actor.AI, confidence=0.75,
            action_type=ActionType.HALT, action_param=0,
        )
        digest = AttestDigest(5, b"\x04" * 32)
        parser = FrameParser()
        parser.feed(encode(req) + encode(digest))
        out = parser.pop_messages()
        assert isinstance(out[0], CommandRequest)
        assert isinstance(out[1], AttestDigest)


# ---------------------------------------------------------------------------
# Regression: a hostile length header used to wedge the parser
# ---------------------------------------------------------------------------

@pytest.mark.regression
class TestOversizedLengthGuard:
    """A header claiming more than MAX_PAYLOAD bytes cannot begin a valid
    frame. Before the guard, the parser buffered forever waiting for bytes
    that never came, and every later frame was swallowed with them: one
    corrupt header took the link down until restart.
    """

    def _hostile_header(self, payload_len: int) -> bytes:
        from ipc.codec import crc16_ccitt
        body = struct.pack("<BBH", MAGIC, int(MsgType.ATTEST_DIGEST), payload_len)
        return body + struct.pack("<H", crc16_ccitt(body))

    def test_parser_recovers_after_an_oversized_header(self):
        parser = FrameParser()
        parser.feed(self._hostile_header(0xFFFF))
        parser.feed(encode(OverrideClear(timestamp_us=7)))
        assert parser.pop_messages() == [OverrideClear(timestamp_us=7)]

    def test_the_rejection_is_recorded(self):
        parser = FrameParser()
        parser.feed(self._hostile_header(0xFFFF))
        assert any("exceeds 255" in e for e in parser.errors)

    def test_a_length_of_exactly_max_payload_is_still_awaited(self):
        """The guard must not reject a frame the protocol permits."""
        parser = FrameParser()
        parser.feed(self._hostile_header(255)[:4])
        assert parser.pop_messages() == []
        assert parser.errors == []

    def test_repeated_hostile_headers_do_not_accumulate(self):
        parser = FrameParser()
        for _ in range(50):
            parser.feed(self._hostile_header(0xFFFF))
        parser.feed(encode(OverrideClear(timestamp_us=1)))
        assert parser.pop_messages() == [OverrideClear(timestamp_us=1)]

    def test_actuation_link_is_protected_too(self):
        """Both links share this parser, so the fix covers the Alvik channel."""
        parser = FrameParser()
        parser.feed(self._hostile_header(0x8000))
        parser.feed(encode(CommandRequest(
            audit_ref=1, timestamp_us=0, actor=Actor.AI, confidence=0.9,
            action_type=ActionType.HALT, action_param=0,
        )))
        assert isinstance(parser.pop_messages()[0], CommandRequest)
