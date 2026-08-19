"""
IPC frame codec: Linux (NPU) ↔ STM32H5 binary protocol.

Implements all 8 message types from docs/ipc-protocol.md v0.1.
Transport-agnostic: encode() produces bytes; decode() consumes bytes.
FrameParser handles incremental UART stream input.

Frame layout (all multi-byte fields little-endian):
  [0]     magic byte 0xA5
  [1]     message type (uint8)
  [2:4]   payload length (uint16 LE)
  [4:4+N] payload (0–255 bytes)
  [4+N:]  CRC-16/CCITT over bytes 0..3+N (uint16 LE)
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

MAGIC: int = 0xA5
MAX_PAYLOAD: int = 255
MAX_FRAME: int = 261  # 4 header + 255 payload + 2 CRC

_HEADER = struct.Struct("<BBH")  # magic, type, payload_len

_CRC_POLY: int = 0x1021
_CRC_INIT: int = 0xFFFF

# Struct for each payload shape
_CMD_REQ = struct.Struct("<QIBfBh")   # 8+4+1+4+1+2 = 20 bytes
_ACK_REJ = struct.Struct("<QB")       # 8+1 = 9 bytes (shared by ACK and REJECT)
_HALT    = struct.Struct("<QB")       # 8+1 = 9 bytes
_STATUS  = struct.Struct("<BBIII")    # 1+1+4+4+4 = 14 bytes


# ---------------------------------------------------------------------------
# Enumerations (reference tables from ipc-protocol.md)
# ---------------------------------------------------------------------------

class MsgType(IntEnum):
    COMMAND_REQUEST = 0x01
    HEARTBEAT       = 0x10
    STATUS_QUERY    = 0x20
    COMMAND_ACK     = 0x81
    COMMAND_REJECT  = 0x82
    HALT_NOTIFY     = 0x90
    HEARTBEAT_ACK   = 0x11
    STATUS_RESPONSE = 0x21


class Actor(IntEnum):
    AI             = 0x01
    HUMAN_OVERRIDE = 0x02


class ActionType(IntEnum):
    NONE          = 0x00
    HALT          = 0x01
    # Robotic arm joints (reserved; not used by Alvik)
    MOVE_JOINT_1  = 0x10
    MOVE_JOINT_2  = 0x11
    MOVE_JOINT_3  = 0x12
    MOVE_JOINT_4  = 0x13
    MOVE_JOINT_5  = 0x14
    MOVE_JOINT_6  = 0x15
    GRIPPER_OPEN  = 0x20
    GRIPPER_CLOSE = 0x21
    # Alvik mobile robot (0x30 range)
    MOVE_FORWARD  = 0x30
    MOVE_BACKWARD = 0x31
    TURN_LEFT     = 0x32
    TURN_RIGHT    = 0x33
    STOP_MOTORS   = 0x34


class AckStatus(IntEnum):
    QUEUED    = 0x00
    EXECUTING = 0x01


class RejectReason(IntEnum):
    KILL_SWITCH_ACTIVE         = 0x01
    CONFIDENCE_BELOW_THRESHOLD = 0x02
    SAFETY_BOUNDARY_VIOLATION  = 0x03
    WATCHDOG_TIMEOUT           = 0x04
    MALFORMED_FRAME            = 0x05
    UNKNOWN_ACTION             = 0x06
    PARAM_OUT_OF_RANGE         = 0x07
    SYSTEM_FAULT               = 0x08
    AUDIT_REF_ZERO             = 0x09


class HaltTrigger(IntEnum):
    KILL_SWITCH_GPIO = 0x01
    WATCHDOG         = 0x02
    SAFETY_BOUNDARY  = 0x03
    LINUX_COMMANDED  = 0x04


class SystemState(IntEnum):
    ARMED  = 0x00
    HALTED = 0x01
    BUSY   = 0x02
    FAULT  = 0x03


# ---------------------------------------------------------------------------
# Message dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandRequest:
    audit_ref: int       # uint64 — must be non-zero (log-before-act)
    timestamp_us: int    # uint32 — microseconds since session start
    actor: Actor
    confidence: float    # float32 IEEE 754
    action_type: ActionType
    action_param: int    # int16
    msg_type: MsgType = MsgType.COMMAND_REQUEST


@dataclass(frozen=True)
class CommandAck:
    audit_ref: int
    status: AckStatus
    msg_type: MsgType = MsgType.COMMAND_ACK


@dataclass(frozen=True)
class CommandReject:
    audit_ref: int
    reason: RejectReason
    msg_type: MsgType = MsgType.COMMAND_REJECT


@dataclass(frozen=True)
class HaltNotify:
    timestamp_us: int    # uint64
    trigger: HaltTrigger
    msg_type: MsgType = MsgType.HALT_NOTIFY


@dataclass(frozen=True)
class Heartbeat:
    msg_type: MsgType = MsgType.HEARTBEAT


@dataclass(frozen=True)
class HeartbeatAck:
    msg_type: MsgType = MsgType.HEARTBEAT_ACK


@dataclass(frozen=True)
class StatusQuery:
    msg_type: MsgType = MsgType.STATUS_QUERY


@dataclass(frozen=True)
class StatusResponse:
    system_state: SystemState
    kill_switch_gpio: int   # 0=NC closed (normal), 1=open (halt)
    commands_received: int  # uint32
    commands_rejected: int  # uint32
    commands_executed: int  # uint32
    msg_type: MsgType = MsgType.STATUS_RESPONSE


Message = (
    CommandRequest | CommandAck | CommandReject | HaltNotify
    | Heartbeat | HeartbeatAck | StatusQuery | StatusResponse
)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class FrameError(ValueError):
    """Raised when a frame fails to decode."""


# ---------------------------------------------------------------------------
# CRC-16/CCITT (IBM-3740 variant: poly=0x1021, init=0xFFFF, no reflection)
# ---------------------------------------------------------------------------

def crc16_ccitt(data: bytes) -> int:
    """Compute CRC-16/CCITT over data. Known vector: crc16_ccitt(b'123456789') == 0x29B1."""
    crc = _CRC_INIT
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ _CRC_POLY) if crc & 0x8000 else (crc << 1)
        crc &= 0xFFFF
    return crc


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------

def encode(msg: Message) -> bytes:
    """Encode a message to a complete IPC frame (header + payload + CRC)."""
    payload = _pack(msg)
    header = _HEADER.pack(MAGIC, int(msg.msg_type), len(payload))
    body = header + payload
    return body + struct.pack("<H", crc16_ccitt(body))


def _pack(msg: Message) -> bytes:
    if isinstance(msg, CommandRequest):
        return _CMD_REQ.pack(
            msg.audit_ref, msg.timestamp_us, int(msg.actor),
            msg.confidence, int(msg.action_type), msg.action_param,
        )
    if isinstance(msg, CommandAck):
        return _ACK_REJ.pack(msg.audit_ref, int(msg.status))
    if isinstance(msg, CommandReject):
        return _ACK_REJ.pack(msg.audit_ref, int(msg.reason))
    if isinstance(msg, HaltNotify):
        return _HALT.pack(msg.timestamp_us, int(msg.trigger))
    if isinstance(msg, StatusResponse):
        return _STATUS.pack(
            int(msg.system_state), msg.kill_switch_gpio,
            msg.commands_received, msg.commands_rejected, msg.commands_executed,
        )
    if isinstance(msg, (Heartbeat, HeartbeatAck, StatusQuery)):
        return b""
    raise TypeError(f"Cannot encode {type(msg).__name__}")


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------

def decode(frame: bytes) -> Message:
    """Decode a complete IPC frame.

    Raises FrameError on bad magic, length mismatch, CRC mismatch,
    unknown message type, or invalid enum value.
    """
    if len(frame) < 6:
        raise FrameError(f"Frame too short: {len(frame)} bytes (minimum 6)")

    magic, type_byte, payload_len = _HEADER.unpack(frame[:4])

    if magic != MAGIC:
        raise FrameError(f"Bad magic byte: 0x{magic:02X} (expected 0xA5)")

    expected = 4 + payload_len + 2
    if len(frame) != expected:
        raise FrameError(
            f"Frame length mismatch: expected {expected} bytes, got {len(frame)}"
        )

    crc_rx = struct.unpack("<H", frame[-2:])[0]
    crc_ok = crc16_ccitt(frame[:-2])
    if crc_rx != crc_ok:
        raise FrameError(
            f"CRC mismatch: received 0x{crc_rx:04X}, computed 0x{crc_ok:04X}"
        )

    try:
        msg_type = MsgType(type_byte)
    except ValueError:
        raise FrameError(f"Unknown message type: 0x{type_byte:02X}") from None

    return _unpack(msg_type, frame[4:4 + payload_len])


def _unpack(msg_type: MsgType, payload: bytes) -> Message:
    _require(payload, 0,  MsgType.HEARTBEAT,       msg_type)
    _require(payload, 0,  MsgType.HEARTBEAT_ACK,   msg_type)
    _require(payload, 0,  MsgType.STATUS_QUERY,     msg_type)
    _require(payload, 20, MsgType.COMMAND_REQUEST,  msg_type)
    _require(payload, 9,  MsgType.COMMAND_ACK,      msg_type)
    _require(payload, 9,  MsgType.COMMAND_REJECT,   msg_type)
    _require(payload, 9,  MsgType.HALT_NOTIFY,      msg_type)
    _require(payload, 14, MsgType.STATUS_RESPONSE,  msg_type)

    if msg_type == MsgType.HEARTBEAT:
        return Heartbeat()
    if msg_type == MsgType.HEARTBEAT_ACK:
        return HeartbeatAck()
    if msg_type == MsgType.STATUS_QUERY:
        return StatusQuery()
    if msg_type == MsgType.COMMAND_REQUEST:
        ref, ts, actor_b, conf, act_b, param = _CMD_REQ.unpack(payload)
        return CommandRequest(
            audit_ref=ref, timestamp_us=ts,
            actor=Actor(actor_b), confidence=conf,
            action_type=ActionType(act_b), action_param=param,
        )
    if msg_type == MsgType.COMMAND_ACK:
        ref, status_b = _ACK_REJ.unpack(payload)
        return CommandAck(audit_ref=ref, status=AckStatus(status_b))
    if msg_type == MsgType.COMMAND_REJECT:
        ref, reason_b = _ACK_REJ.unpack(payload)
        return CommandReject(audit_ref=ref, reason=RejectReason(reason_b))
    if msg_type == MsgType.HALT_NOTIFY:
        ts, trigger_b = _HALT.unpack(payload)
        return HaltNotify(timestamp_us=ts, trigger=HaltTrigger(trigger_b))
    if msg_type == MsgType.STATUS_RESPONSE:
        state_b, gpio, recv, rej, exe = _STATUS.unpack(payload)
        return StatusResponse(
            system_state=SystemState(state_b), kill_switch_gpio=gpio,
            commands_received=recv, commands_rejected=rej, commands_executed=exe,
        )
    raise FrameError(f"No decoder for {msg_type!r}")  # unreachable after _require guards


def _require(payload: bytes, length: int, target: MsgType, actual: MsgType) -> None:
    if actual == target and len(payload) != length:
        raise FrameError(
            f"{target.name}: expected {length} payload bytes, got {len(payload)}"
        )


# ---------------------------------------------------------------------------
# Stream parser (incremental, for UART input)
# ---------------------------------------------------------------------------

class FrameParser:
    """Incremental frame parser. Feed arbitrary byte chunks from a UART stream;
    decoded messages accumulate and are drained with pop_messages().

    Garbage bytes before the magic byte are silently discarded, matching the
    receiver behaviour defined in ipc-protocol.md.
    """

    def __init__(self) -> None:
        self._buf: bytearray = bytearray()
        self.messages: list[Message] = []
        self.errors: list[str] = []

    def feed(self, data: bytes | bytearray) -> None:
        self._buf.extend(data)
        self._process()

    def pop_messages(self) -> list[Message]:
        out, self.messages = self.messages, []
        return out

    def _process(self) -> None:
        while self._buf:
            # Seek the magic byte
            if self._buf[0] != MAGIC:
                idx = self._buf.find(MAGIC)
                if idx == -1:
                    self._buf.clear()
                    return
                del self._buf[:idx]

            if len(self._buf) < 4:
                return  # wait for full header

            _, _, payload_len = _HEADER.unpack(bytes(self._buf[:4]))
            frame_len = 4 + payload_len + 2

            if len(self._buf) < frame_len:
                return  # wait for full frame

            frame = bytes(self._buf[:frame_len])
            del self._buf[:frame_len]

            try:
                self.messages.append(decode(frame))
            except FrameError as exc:
                self.errors.append(str(exc))
                # Discard the magic byte and retry from next position
                if self._buf and self._buf[0] == MAGIC:
                    pass  # already consumed; continue
