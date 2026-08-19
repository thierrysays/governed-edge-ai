"""
IPC codec subset for Alvik MicroPython firmware.

Implements the receiver side of the binary IPC protocol:
  - parse incoming CommandRequest frames from VENTUNO Q
  - encode CommandAck / CommandReject responses

Frame layout (matches linux-stack/ipc/codec.py exactly):
  [0]     magic 0xA5
  [1]     message type (uint8)
  [2:4]   payload length (uint16 LE)
  [4:N]   payload
  [N:N+2] CRC-16/CCITT (uint16 LE) over bytes 0..N-1

CommandRequest payload (20 bytes, struct "<QIBfBh"):
  audit_ref    uint64  8 bytes
  timestamp_us uint32  4 bytes
  actor        uint8   1 byte
  confidence   float32 4 bytes
  action_type  uint8   1 byte
  action_param int16   2 bytes

CommandAck payload (9 bytes, struct "<QB"):
  audit_ref uint64 8 bytes
  status    uint8  1 byte

CommandReject payload (9 bytes, struct "<QB"):
  audit_ref uint64 8 bytes
  reason    uint8  1 byte
"""

import struct

MAGIC = 0xA5

# Message types
MSG_COMMAND_REQUEST = 0x01
MSG_HEARTBEAT       = 0x10
MSG_STATUS_QUERY    = 0x20
MSG_COMMAND_ACK     = 0x81
MSG_COMMAND_REJECT  = 0x82
MSG_HALT_NOTIFY     = 0x90
MSG_HEARTBEAT_ACK   = 0x11
MSG_STATUS_RESPONSE = 0x21

# ActionType codes (Alvik mobile robot subset)
ACTION_NONE         = 0x00
ACTION_HALT         = 0x01
ACTION_MOVE_FORWARD = 0x30
ACTION_MOVE_BACKWARD= 0x31
ACTION_TURN_LEFT    = 0x32
ACTION_TURN_RIGHT   = 0x33
ACTION_STOP_MOTORS  = 0x34

# AckStatus
ACK_QUEUED    = 0x00
ACK_EXECUTING = 0x01

# RejectReason
REJ_KILL_SWITCH_ACTIVE         = 0x01
REJ_CONFIDENCE_BELOW_THRESHOLD = 0x02
REJ_SAFETY_BOUNDARY_VIOLATION  = 0x03
REJ_WATCHDOG_TIMEOUT           = 0x04
REJ_MALFORMED_FRAME            = 0x05
REJ_UNKNOWN_ACTION             = 0x06
REJ_PARAM_OUT_OF_RANGE         = 0x07
REJ_SYSTEM_FAULT               = 0x08
REJ_AUDIT_REF_ZERO             = 0x09

CONFIDENCE_THRESHOLD = 0.70  # float32 gate, matches linux-side gate

_HEADER = struct.Struct("<BBH")   # magic, type, payload_len
_CMD_REQ = struct.Struct("<QIBfBh")  # 20 bytes
_ACK_REJ = struct.Struct("<QB")   # 9 bytes

_CRC_POLY = 0x1021
_CRC_INIT = 0xFFFF


def _crc16(data: bytes) -> int:
    crc = _CRC_INIT
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ _CRC_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class FrameParser:
    """Incremental parser for the IPC frame stream."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._messages = []

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)
        self._parse()

    def _parse(self) -> None:
        buf = self._buf
        while True:
            # Find magic byte
            idx = 0
            while idx < len(buf) and buf[idx] != MAGIC:
                idx += 1
            if idx:
                del buf[:idx]

            if len(buf) < 4:
                break

            _, msg_type, payload_len = _HEADER.unpack_from(buf, 0)
            frame_len = 4 + payload_len + 2
            if len(buf) < frame_len:
                break

            frame = bytes(buf[:frame_len])
            body = frame[:-2]
            crc_recv = struct.unpack_from("<H", frame, frame_len - 2)[0]
            crc_calc = _crc16(body)

            del buf[:frame_len]

            if crc_recv != crc_calc:
                continue  # discard corrupted frame

            payload = frame[4:4 + payload_len]
            msg = _decode(msg_type, payload)
            if msg is not None:
                self._messages.append(msg)

    def pop_messages(self) -> list:
        msgs = self._messages[:]
        self._messages.clear()
        return msgs


class CommandRequest:
    __slots__ = ("audit_ref", "timestamp_us", "actor", "confidence",
                 "action_type", "action_param")

    def __init__(self, audit_ref, timestamp_us, actor, confidence,
                 action_type, action_param):
        self.audit_ref = audit_ref
        self.timestamp_us = timestamp_us
        self.actor = actor
        self.confidence = confidence
        self.action_type = action_type
        self.action_param = action_param


def _decode(msg_type: int, payload: bytes):
    if msg_type == MSG_COMMAND_REQUEST and len(payload) == 20:
        ref, ts, actor, conf, act, param = _CMD_REQ.unpack(payload)
        return CommandRequest(ref, ts, actor, conf, act, param)
    return None


def encode_ack(audit_ref: int) -> bytes:
    payload = _ACK_REJ.pack(audit_ref, ACK_EXECUTING)
    return _make_frame(MSG_COMMAND_ACK, payload)


def encode_reject(audit_ref: int, reason: int) -> bytes:
    payload = _ACK_REJ.pack(audit_ref, reason)
    return _make_frame(MSG_COMMAND_REJECT, payload)


def encode_halt_notify(audit_ref: int) -> bytes:
    payload = _ACK_REJ.pack(audit_ref, 0)
    return _make_frame(MSG_HALT_NOTIFY, payload)


def _make_frame(msg_type: int, payload: bytes) -> bytes:
    header = _HEADER.pack(MAGIC, msg_type, len(payload))
    body = header + payload
    crc = _crc16(body)
    return body + struct.pack("<H", crc)
