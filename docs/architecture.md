# governed-edge-ai — Architecture & Functional Specification

**Version:** 1.0 — 2026-08-19
**Status:** Steps 1–6 fully implemented and tested. Steps 7–8 planned.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Hardware Architecture](#2-hardware-architecture)
3. [Three-Tier Software Architecture](#3-three-tier-software-architecture)
4. [Component Reference](#4-component-reference)
   - 4.1 IPC Codec
   - 4.2 MockSTM32H5
   - 4.3 Perception Pipeline
   - 4.4 Governance Filter
   - 4.5 Audit Logger
   - 4.6 Audit Dashboard
5. [IPC Protocol Specification](#5-ipc-protocol-specification)
6. [Audit Data Model](#6-audit-data-model)
7. [Governance Contract](#7-governance-contract)
8. [Sequence Diagrams](#8-sequence-diagrams)
9. [Configuration & Deployment](#9-configuration--deployment)
10. [Test Architecture](#10-test-architecture)
11. [Planned Extensions (Steps 7–8)](#11-planned-extensions-steps-78)
12. [Open Decisions](#12-open-decisions)

---

## 1. System Overview

**governed-edge-ai** demonstrates that AI governance invariants can be enforced at the hardware and protocol level — not merely documented as policy. The central claim: in a physical AI system, no actuator command should be executable without a prior, confirmed audit log entry. This project implements and proves that claim across three physical boards.

### Core invariant

```
∀ actuation: ∃ audit_ref ≥ 1  such that  log_event(audit_ref) precedes  transmit(CommandRequest)
```

The STM32 co-processor on the actuation side enforces this by rejecting any `CommandRequest` frame where `audit_ref == 0`. The constraint is structural, not procedural.

### What is implemented (Steps 1–6)

| Step | Module | Description |
|---|---|---|
| 1 | `audit-service/logger.py` | Append-only SQLite audit logger |
| 2 | `linux-stack/ipc/codec.py` | Binary IPC protocol codec |
| 3 | `audit-service/dashboard/` | Read-only audit dashboard |
| 4 | `linux-stack/ipc/mock_peer.py` | Hardware simulator (pty-based) |
| 5 | `linux-stack/perception/` | Perception pipeline interface + stub backends |
| 6 | `linux-stack/governance/filter.py` | Governance filter (the safety gate) |

### What is planned (Steps 7–8)

| Step | Module | Description |
|---|---|---|
| 7 | `alvik-firmware/` | Alvik firmware: receive `CommandRequest`, execute motors, return `CommandAck/Reject` |
| 8 | `uno-q-perception/` | UNO Q 4GB perception service: camera capture via ISP, run models, send `DetectionResult` to VENTUNO Q |

---

## 2. Hardware Architecture

### Boards

| Board | Processor A | Processor B | Key peripherals | Role |
|---|---|---|---|---|
| **Arduino UNO Q 4GB** | Qualcomm QRB2210 (quad-core Cortex-A53, 2 GHz) | STM32U585 (Cortex-M33, 160 MHz) | Dual ISP 13 MP / 30 fps, 4 GB LPDDR4, 32 GB eMMC, Wi-Fi 5, BT 5.1 | Perception node |
| **Arduino VENTUNO Q** | Qualcomm IQ-8275 NPU (40 TOPS, Cortex-A55) | STM32H5 (Cortex-M33, 250 MHz) | 16 GB LPDDR5, Wi-Fi 6E, BT 5.3 | Governance brain |
| **Arduino Alvik** | ESP32-S3 (Xtensa LX7, 240 MHz) | STM32F411RC (Cortex-M4, 100 MHz) | ToF 8×8 (VL53L7CX, 350 cm), IMU 6-axis (LSM6DSOX), colour (APDS-9660), 18650 battery, LEGO connector | Physical body |

All three boards are owned. No external actuator required beyond Alvik's built-in drivetrain.

### Physical connectivity

```
┌─────────────────────┐      Wi-Fi / USB-C      ┌──────────────────────┐
│    UNO Q 4GB        │ ─────────────────────►  │    VENTUNO Q         │
│  Perception Node    │   DetectionResult JSON   │  Governance Brain    │
│  QRB2210 + STM32U5  │                          │  IQ8 NPU + STM32H5  │
│  Dual ISP cameras   │                          │  AuditLogger SQLite  │
└─────────────────────┘                          └──────────┬───────────┘
                                                            │
                                                    USB-C / UART
                                                    CommandRequest
                                                            │
                                                 ┌──────────▼───────────┐
                                                 │    Alvik             │
                                                 │  Physical Body       │
                                                 │  ESP32-S3 + STM32F411│
                                                 │  Motors + ToF + IMU  │
                                                 └──────────────────────┘
```

**UNO Q 4GB → VENTUNO Q transport (open decision):** Wi-Fi UDP or USB-C UART. Wi-Fi avoids cable, USB-C gives deterministic latency.

**VENTUNO Q → Alvik transport:** USB-C serial (primary). The Alvik's USB-C port presents as a serial device to the host.

---

## 3. Three-Tier Software Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  TIER 1 — PERCEPTION  (UNO Q 4GB, Qualcomm QRB2210 Linux)           │
│                                                                      │
│  Camera (ISP)  ──►  PerceptionPipeline  ──►  DetectionResult[]      │
│   YOLO-X (object)    MediaPipe (gesture)    PoseNet (pose)           │
│                                                                      │
│  Output: list[DetectionResult] sent to Tier 2 over network/serial   │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │  DetectionResult[]
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  TIER 2 — GOVERNANCE  (VENTUNO Q, Qualcomm IQ8 NPU + STM32H5)       │
│                                                                      │
│  GovernanceFilter                                                    │
│    ├─ sort by confidence (desc)                                      │
│    ├─ for each detection:                                            │
│    │   ├─ AuditLogger.log_event()  ──►  audit_ref ≥ 1              │
│    │   └─ if highest & above threshold:                             │
│    │       ├─ encode CommandRequest(audit_ref, ...)                 │
│    │       ├─ channel.write(frame)                                  │
│    │       └─ AuditLogger.update_stm32_ack(ack)                    │
│    └─ STM32H5: independent confidence gate (float32)                │
│                                                                      │
│  AuditLogger  ──►  SQLite WAL  (audit_log, sessions)                │
│  Dashboard    ──►  Flask read-only API                              │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │  CommandRequest frame (binary IPC)
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  TIER 3 — ACTUATION  (Alvik, ESP32-S3 + STM32F411)                  │
│                                                                      │
│  STM32F411 receives CommandRequest                                   │
│    ├─ validate audit_ref ≠ 0                                         │
│    ├─ validate confidence ≥ threshold (float32 gate)                │
│    ├─ check kill-switch GPIO state                                   │
│    ├─ if valid: execute motor command, return CommandAck             │
│    └─ if invalid: return CommandReject(reason)                      │
│                                                                      │
│  Hardware: drivetrain motors, ToF 8×8, 6-axis IMU                   │
└──────────────────────────────────────────────────────────────────────┘
```

### Data flow summary

```
Camera frame
  → DetectionResult(type, label, confidence)          [Tier 1]
  → AuditEvent logged → audit_ref returned            [Tier 2]
  → CommandRequest(audit_ref, confidence, action)     [Tier 2 → Tier 3]
  → CommandAck | CommandReject(reason)                [Tier 3 → Tier 2]
  → update_stm32_ack(audit_ref, ack)                  [Tier 2]
```

---

## 4. Component Reference

### 4.1 IPC Codec (`linux-stack/ipc/codec.py`)

Transport-agnostic binary frame codec. `encode()` produces bytes; `FrameParser` handles incremental UART stream input across multiple `read()` calls.

**Enumerations:**

| Enum | Values |
|---|---|
| `MsgType` | `COMMAND_REQUEST=0x01`, `HEARTBEAT=0x10`, `STATUS_QUERY=0x20`, `COMMAND_ACK=0x81`, `COMMAND_REJECT=0x82`, `HALT_NOTIFY=0x90`, `HEARTBEAT_ACK=0x11`, `STATUS_RESPONSE=0x21` |
| `Actor` | `AI=0x01`, `HUMAN_OVERRIDE=0x02` |
| `ActionType` | `NONE=0x00`, `HALT=0x01`, `MOVE_JOINT_1..6=0x10..0x15`, `GRIPPER_OPEN=0x20`, `GRIPPER_CLOSE=0x21` |
| `AckStatus` | `QUEUED=0x00`, `EXECUTING=0x01` |
| `RejectReason` | `KILL_SWITCH_ACTIVE=0x01`, `CONFIDENCE_BELOW_THRESHOLD=0x02`, `SAFETY_BOUNDARY_VIOLATION=0x03`, `WATCHDOG_TIMEOUT=0x04`, `MALFORMED_FRAME=0x05`, `UNKNOWN_ACTION=0x06`, `PARAM_OUT_OF_RANGE=0x07`, `SYSTEM_FAULT=0x08`, `AUDIT_REF_ZERO=0x09` |
| `HaltTrigger` | `KILL_SWITCH_GPIO=0x01`, `WATCHDOG=0x02`, `SAFETY_BOUNDARY=0x03`, `LINUX_COMMANDED=0x04` |
| `SystemState` | `ARMED=0x00`, `HALTED=0x01`, `BUSY=0x02`, `FAULT=0x03` |

**Message dataclasses:**

| Class | Fields |
|---|---|
| `CommandRequest` | `audit_ref: int` (uint64, ≥1), `timestamp_us: int` (uint32), `actor: Actor`, `confidence: float` (float32), `action_type: ActionType`, `action_param: int` (int16) |
| `CommandAck` | `audit_ref: int`, `status: AckStatus` |
| `CommandReject` | `audit_ref: int`, `reason: RejectReason` |
| `HaltNotify` | `timestamp_us: int` (uint64), `trigger: HaltTrigger` |
| `StatusResponse` | `system_state: SystemState`, `kill_switch_gpio: int`, `commands_received: int`, `commands_rejected: int`, `commands_executed: int` |
| `Heartbeat`, `HeartbeatAck`, `StatusQuery` | (no payload fields) |

---

### 4.2 MockSTM32H5 (`linux-stack/ipc/mock_peer.py`)

Unix pty-based hardware simulator. Opened as a file descriptor — identical interface to a real UART device. Runs a reader thread that processes incoming frames, enforces the same state machine as the real firmware, and writes responses.

**State machine:**

```
         trigger_kill_switch()
ARMED ──────────────────────────► HALTED
  │                                  │
  │ CommandRequest (valid)            │ reset() [planned]
  ▼                                  │
BUSY ────────────────────────────────┘
  │
  │ execution complete
  ▼
ARMED

FAULT (any system error — terminal state until reset)
```

**Confidence gate:** threshold 0.70 in float32. The mock applies this independently of the Linux gate. Sending `confidence=0.70` (float64) encodes to slightly below 0.70 in float32 on the wire — the mock rejects it (`CONFIDENCE_BELOW_THRESHOLD`).

**Watchdog:** configurable `watchdog_ms`. If no `Heartbeat` is received within the window, the mock transitions to HALTED and sends `HaltNotify(WATCHDOG)`.

**Usage:**

```python
with MockSTM32H5(watchdog_ms=10_000.0) as peer:
    channel = open(peer.device, "rb+", buffering=0)
    # channel is now binary r/w, identical to a real UART
    peer.trigger_kill_switch()   # put into HALTED state
```

---

### 4.3 Perception Pipeline (`linux-stack/perception/`)

**`DetectionResult`** — frozen dataclass, the unit of perception output:

```python
@dataclass(frozen=True)
class DetectionResult:
    detection_type: str    # "object" | "gesture" | "pose"
    label: str             # e.g. "person", "thumbs_up", "proximity_breach"
    confidence: float      # clamped to [0.0, 1.0]

    def passes_threshold(self, threshold: float) -> bool: ...
```

**`PerceptionPipeline`** — ABC enforcing `run(frame) -> list[DetectionResult]`. Swap any backend without touching the governance layer.

**Stub backends (for testing without cameras or models):**

| Class | Label | Default confidence |
|---|---|---|
| `StubObjectDetector` | `person` / `object` | configurable |
| `StubGestureRecognizer` | `thumbs_up` | configurable |
| `StubPoseEstimator` | `proximity_breach` | configurable |
| `NullPipeline` | (none) | — |

**Planned real backends (Step 8):**

| Backend | Model | Detection types |
|---|---|---|
| YOLO-X (NPU-accelerated) | Qualcomm AI Hub runtime | object: person, robot_part, tool |
| MediaPipe | CPU or NPU | gesture: stop, thumbs_up, thumbs_down |
| PoseNet | NPU | pose: proximity_breach |

---

### 4.4 Governance Filter (`linux-stack/governance/filter.py`)

The central safety gate. Sits between the perception pipeline output and the IPC channel. See [Section 7](#7-governance-contract) for the full governance contract.

**Default command map** (safety-conservative — unknown labels default to HALT):

| Label | Detection type | Command | Rationale |
|---|---|---|---|
| `person` | object | HALT | Person in workspace |
| `robot_part` | object | HALT | Self-collision risk |
| `tool` | object | HALT | Foreign object |
| `stop` | gesture | HALT | Operator stop command |
| `thumbs_up` | gesture | GRIPPER_OPEN | Explicit operator permission |
| `thumbs_down` | gesture | GRIPPER_CLOSE | Explicit operator close command |
| `proximity_breach` | pose | HALT | Safety boundary violation |
| *(anything else)* | any | HALT | Unknown = unsafe |

**Constructor parameters:**

```python
GovernanceFilter(
    logger: AuditLogger,
    session_id: str,
    channel: BinaryIO,              # unbuffered binary r/w (UART or pty)
    confidence_threshold: float = 0.70,
    command_map: dict | None = None,  # None → DEFAULT_COMMAND_MAP
    response_timeout_s: float = 0.5,
)
```

**`process_frame(detections: list[DetectionResult]) → None`**

1. Return immediately if `detections` is empty.
2. Sort by `confidence` descending.
3. For each detection:
   a. Resolve `(action_type, action_param)` from command map.
   b. Determine `should_send`: True only for the first detection that passes the threshold.
   c. Call `logger.log_event(AuditEvent(..., command_sent=should_send, stm32_ack=None))` → `audit_ref`.
   d. If `should_send`: call `_send_command(detection, action_type, action_param, audit_ref)`.
   e. If response received: call `logger.update_stm32_ack(audit_ref, ack)`.

**`_read_response(audit_ref) → bool | None`**

Polls the channel using `select.select` with 50 ms slices until `response_timeout_s`. Returns `True` (ACK), `False` (REJECT), or `None` (timeout / I/O error). Feeds unrelated frames into `FrameParser` for later retrieval.

---

### 4.5 Audit Logger (`audit-service/logger.py`)

Append-only SQLite WAL-mode log. The write path is the only path that creates or modifies rows; the dashboard is read-only.

**Key methods:**

| Method | Returns | Description |
|---|---|---|
| `open_session(board_serial, notes)` | `str` (UUID) | Creates a session row, returns session_id |
| `close_session(session_id, notes)` | `None` | Sets `ended_at` on the session |
| `log_event(AuditEvent)` | `int` (rowid ≥ 1) | Inserts audit row, returns `audit_ref` |
| `update_stm32_ack(event_id, ack)` | `None` | Sets `stm32_ack` once; idempotent if already set |
| `flag_event(event_id, notes)` | `None` | Sets `flag=1`; one-way, never reset |

**Guarantees:**
- `log_event()` returns a SQLite rowid which is always ≥ 1. A zero return is impossible under normal SQLite operation.
- `update_stm32_ack()` only writes if `stm32_ack IS NULL` — prevents overwriting a confirmed ACK.
- `flag_event()` uses `SET flag = 1` — cannot move 1 → 0.
- No `DELETE` or truncation statements exist anywhere in the codebase.

---

### 4.6 Audit Dashboard (`audit-service/dashboard/`)

Flask + SQLAlchemy read-only interface over the audit log.

**Views:**

| View | Description |
|---|---|
| Session list | All sessions with board serial, start/end time, event count |
| Event stream | Chronological log: ts, actor, label, confidence, command, sent/suppressed, ACK/REJECT/NULL |
| Suppression rate | Detections below threshold vs total, per session |
| Flagged events | Events with `flag=1`, available for human review |

**Access:** local network only. No write endpoints. No authentication (local-only assumption; production would require mTLS or equivalent).

---

## 5. IPC Protocol Specification

### Frame layout

All multi-byte fields are little-endian.

```
Byte  0      : Magic byte = 0xA5
Byte  1      : Message type (uint8)
Bytes 2–3    : Payload length N (uint16 LE)
Bytes 4..4+N : Payload (0–255 bytes)
Bytes 4+N..  : CRC-16/CCITT over bytes 0..3+N (uint16 LE)
```

Maximum frame size: 261 bytes (4 header + 255 payload + 2 CRC).

### CRC

Polynomial: `0x1021`. Initial value: `0xFFFF`. Computed over all bytes from the magic byte through the last payload byte inclusive.

### Payload structures (little-endian)

| Message | Type byte | Payload struct | Size |
|---|---|---|---|
| `CommandRequest` | `0x01` | `uint64 audit_ref, uint32 timestamp_us, uint8 actor, float32 confidence, uint8 action_type, int16 action_param` | 20 bytes |
| `CommandAck` | `0x81` | `uint64 audit_ref, uint8 status` | 9 bytes |
| `CommandReject` | `0x82` | `uint64 audit_ref, uint8 reason` | 9 bytes |
| `HaltNotify` | `0x90` | `uint64 timestamp_us, uint8 trigger` | 9 bytes |
| `StatusResponse` | `0x21` | `uint8 state, uint8 kill_switch, uint32 received, uint32 rejected, uint32 executed` | 14 bytes |
| `Heartbeat` | `0x10` | (none) | 0 bytes |
| `HeartbeatAck` | `0x11` | (none) | 0 bytes |
| `StatusQuery` | `0x20` | (none) | 0 bytes |

### Critical field: `audit_ref`

- Encoded as `uint64` in the `CommandRequest` payload.
- Value `0` is reserved and rejected by the STM32 firmware with `RejectReason.AUDIT_REF_ZERO`.
- The Linux side obtains `audit_ref` from `AuditLogger.log_event()`, which returns a SQLite rowid (always ≥ 1). The value 0 is structurally impossible in normal operation.

### Confidence float32 edge case

The `confidence` field is encoded as IEEE 754 float32 on the wire. The value `0.70` in Python (float64) is approximately `0.6999999999999999555910790149937383830547332763671875`. Encoded as float32, it becomes `0.699999988079071044921875` — slightly below `0.70`. The STM32 dual-layer gate, operating on the float32 value, rejects this as `CONFIDENCE_BELOW_THRESHOLD` even if the Linux gate passed it. This is the intended defence-in-depth behaviour, and is covered by a regression test (`test_low_confidence_float32_round_trip_rejected_by_stm32`).

---

## 6. Audit Data Model

### `sessions` table

| Column | Type | Description |
|---|---|---|
| `session_id` | TEXT PK | UUID, one per power cycle |
| `started_at` | TEXT | ISO 8601 UTC |
| `ended_at` | TEXT (nullable) | ISO 8601 UTC |
| `board_serial` | TEXT (nullable) | Board identifier |
| `notes` | TEXT (nullable) | Free-text |

### `audit_log` table

| Column | Type | Constraint | Description |
|---|---|---|---|
| `id` | INTEGER PK | AUTOINCREMENT | The `audit_ref` carried in `CommandRequest` |
| `ts` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `session_id` | TEXT | NOT NULL | FK → `sessions` |
| `actor` | TEXT | `'ai'` \| `'human_override'` | Who initiated the event |
| `detection_type` | TEXT | `'object'` \| `'gesture'` \| `'pose'` | Perception modality |
| `detection_label` | TEXT | NOT NULL | e.g. `person`, `thumbs_up` |
| `confidence` | REAL | 0.0–1.0 | Model confidence score |
| `command` | TEXT | NOT NULL | Command name e.g. `HALT`, `GRIPPER_OPEN` |
| `command_sent` | INTEGER | 0 \| 1 | Whether the command was transmitted |
| `stm32_ack` | INTEGER (nullable) | 0 \| 1 \| NULL | MCU response: 1=ACK, 0=REJECT, NULL=no response |
| `flag` | INTEGER | 0 \| 1, default 0 | Human review flag (one-way: 0→1 only) |
| `notes` | TEXT (nullable) | — | Annotations |

### Indexes

| Index | Columns | Purpose |
|---|---|---|
| `idx_audit_ts` | `ts` | Time-range queries |
| `idx_audit_actor` | `actor` | Actor-based filtering |
| `idx_audit_flag` | `flag WHERE flag=1` | Partial index for flagged events |

### Forensic semantics of `stm32_ack`

| Value | Meaning |
|---|---|
| `NULL` | `command_sent=0`: command was suppressed (below threshold or not highest confidence). `command_sent=1`: command was sent but no response arrived before `response_timeout_s`. Both are forensically distinct from a confirmed rejection. |
| `0` (False) | MCU received the command and explicitly rejected it (kill switch, confidence gate, malformed frame, etc.) |
| `1` (True) | MCU received the command and acknowledged execution |

---

## 7. Governance Contract

Six invariants enforced by `GovernanceFilter`. All are structural, not procedural.

### Invariant 1: Log-before-act

`audit_ref` is obtained by calling `logger.log_event()` before any `CommandRequest` frame is written to the channel. The `_send_command()` call is inside the `if should_send:` block that follows the log call. There is no code path that transmits without logging.

```python
audit_ref = self._logger.log_event(AuditEvent(...))  # must succeed first
if should_send:
    ack = self._send_command(..., audit_ref)           # only then
```

### Invariant 2: No log, no command

If `log_event()` raises (disk full, database locked, schema violation), the exception propagates out of `process_frame()`. No `CommandRequest` is transmitted. The caller is responsible for handling the exception and deciding whether to retry or halt.

### Invariant 3: Confidence gate (Linux side)

`should_send` is `False` for any detection where `detection.passes_threshold(self._threshold)` returns `False`. Suppressed detections are still logged with `command_sent=False` — the suppression decision is on record for forensic analysis.

### Invariant 4: One command per frame

`command_sent_this_frame` is a boolean that flips to `True` after the first detection is transmitted. All subsequent detections in the same frame are logged with `command_sent=False` regardless of their confidence score.

### Invariant 5: Dual-layer confidence gate

The Linux gate operates on float64 values. The STM32H5 gate operates on float32 values received on the wire. They are independent: a detection can pass the Linux gate and be rejected by the MCU gate (as in the float32 rounding edge case at exactly 0.70). Neither gate can be bypassed by the other side.

### Invariant 6: ACK/REJECT tracking

`update_stm32_ack()` is called at most once per transmitted command, after `_read_response()` returns. If `_read_response()` returns `None` (timeout or I/O error), `update_stm32_ack()` is not called and `stm32_ack` remains `NULL` in the audit log. The `NULL` state is forensically meaningful and distinguishable from a confirmed rejection.

---

## 8. Sequence Diagrams

### 8.1 Normal accept flow (single detection, above threshold)

```
Perception       GovernanceFilter      AuditLogger       STM32H5
────────────     ────────────────      ───────────       ────────
     │                  │                  │                 │
     │ DetectionResult  │                  │                 │
     │  label="person"  │                  │                 │
     │  confidence=0.91 │                  │                 │
     │─────────────────►│                  │                 │
     │                  │ log_event()      │                 │
     │                  │─────────────────►│                 │
     │                  │   audit_ref=42   │                 │
     │                  │◄─────────────────│                 │
     │                  │                  │                 │
     │                  │ encode + write CommandRequest      │
     │                  │  audit_ref=42, confidence=0.91    │
     │                  │────────────────────────────────►  │
     │                  │                  │                 │ validate
     │                  │                  │                 │ audit_ref≠0 ✓
     │                  │                  │                 │ confidence≥0.70 ✓
     │                  │                  │                 │ kill_switch=NC ✓
     │                  │   CommandAck(audit_ref=42)        │
     │                  │◄───────────────────────────────── │
     │                  │                  │                 │
     │                  │ update_stm32_ack(42, True)        │
     │                  │─────────────────►│                 │
     │                  │                  │                 │
```

### 8.2 Suppression flow (below threshold)

```
Perception       GovernanceFilter      AuditLogger
────────────     ────────────────      ───────────
     │                  │                  │
     │ DetectionResult  │                  │
     │  confidence=0.30 │                  │
     │─────────────────►│                  │
     │                  │ log_event(       │
     │                  │  command_sent=False)
     │                  │─────────────────►│
     │                  │   audit_ref=43   │
     │                  │◄─────────────────│
     │                  │                  │
     │         [NO CommandRequest transmitted]
     │         [stm32_ack remains NULL]    │
```

### 8.3 Multi-detection frame (one command per frame)

```
Detections: [person@0.91, proximity_breach@0.76, tool@0.40]
After sort:  person@0.91  →  proximity_breach@0.76  →  tool@0.40

person@0.91:          log → command_sent=True  → CommandRequest sent → CommandAck
proximity_breach@0.76 log → command_sent=False [already sent one this frame]
tool@0.40:            log → command_sent=False [below threshold]
```

### 8.4 Kill-switch rejection flow

```
GovernanceFilter      AuditLogger       STM32H5 (HALTED state)
────────────────      ───────────       ──────────────────────
     │                    │                       │
     │ log_event()        │                       │
     │───────────────────►│                       │
     │   audit_ref=44     │                       │
     │◄───────────────────│                       │
     │                    │                       │
     │ CommandRequest(audit_ref=44)               │
     │────────────────────────────────────────►  │
     │                    │    kill_switch=ACTIVE │
     │   CommandReject(KILL_SWITCH_ACTIVE)        │
     │◄───────────────────────────────────────── │
     │                    │                       │
     │ update_stm32_ack(44, False)               │
     │───────────────────►│                       │
```

### 8.5 Timeout flow (no peer response)

```
GovernanceFilter      AuditLogger       Channel (unresponsive)
────────────────      ───────────       ─────────────────────
     │                    │                    │
     │ log_event()        │                    │
     │───────────────────►│                    │
     │   audit_ref=45     │                    │
     │◄───────────────────│                    │
     │                    │                    │
     │ channel.write(CommandRequest)           │
     │───────────────────────────────────────►│
     │                    │     [no response]  │
     │ select.select()... timeout after 0.5s  │
     │ _read_response() → None                │
     │                    │                    │
     │ [update_stm32_ack NOT called]          │
     │ [stm32_ack stays NULL in audit_log]    │
```

---

## 9. Configuration & Deployment

### sys.path requirements

The `audit-service` package (`logger.py`) is imported by the `linux-stack` governance filter. The two packages live in separate directories. Both production launch scripts and CI must set `PYTHONPATH`:

```bash
# Production (VENTUNO Q Linux side)
export PYTHONPATH=/opt/governed-edge-ai/audit-service
python3 /opt/governed-edge-ai/linux-stack/main.py

# Tests (conftest.py handles this automatically)
# linux-stack/tests/conftest.py:
#   sys.path.insert(0, str(_ROOT / "audit-service"))
```

### Makefile targets

```bash
make smoke        # Fast sanity pass (smoke-marked tests only, no coverage)
make test         # Full test suite + coverage
make lint         # ruff check (security rules included)
make typecheck    # mypy (production files only, PYTHONPATH set)
make security     # bandit SAST + pip-audit CVE scan
make qa           # lint + typecheck + security + test  (CI gate)
```

Per-module variants: `make audit-test`, `make linux-test`, `make linux-lint`, etc.

### Channel configuration

The `GovernanceFilter` accepts any `BinaryIO` opened in unbuffered binary mode (`buffering=0`). In production this is the UART device to the STM32H5:

```python
channel = open("/dev/ttyS0", "rb+", buffering=0)
gov = GovernanceFilter(logger=audit_logger, session_id=sid, channel=channel)
```

In tests it is the mock peer pty:

```python
with MockSTM32H5(watchdog_ms=10_000.0) as peer:
    channel = open(peer.device, "rb+", buffering=0)
```

### Audit database

SQLite WAL mode, `synchronous=NORMAL`. Suitable for the demonstrator. Production considerations:

- Move the database file to dedicated storage (separate from OS volume)
- Add cryptographic signing of log entries (current implementation is tamper-evident, not tamper-proof)
- Consider `synchronous=FULL` for stronger durability guarantees

---

## 10. Test Architecture

### Overview

```
184 tests total · 97% line coverage · hardware-free
```

All tests run without physical hardware. The `MockSTM32H5` provides the STM32H5 simulation via a Unix pty.

### Test suites

| File | Tests | Class | Coverage area |
|---|---|---|---|
| `test_smoke_governance.py` | 7 | smoke | End-to-end sanity: import, accept flow, null pipeline, suppression, kill switch, multi-backend, log-before-act |
| `test_governance.py` | 36 | unit | Empty frame, confidence gate, command mapping, multi-detection frame, log-before-act integrity, reject paths, timeout |
| `test_ipc_codec.py` | ~40 | unit | Encode/decode round-trips, CRC validation, FrameParser incremental input, all message types |
| `test_mock_peer.py` | ~40 | unit | State machine, kill switch, confidence gate (float32), watchdog, reject reasons |
| `test_perception.py` | ~30 | unit | DetectionResult validation, threshold, stub backends, NullPipeline |
| `test_smoke_ipc.py` | ~15 | smoke | IPC codec smoke: encode/decode, frame corruption detection |
| `test_smoke_mock.py` | ~10 | smoke | Mock peer smoke: connect, send, receive ACK |
| `test_smoke_perception.py` | ~6 | smoke | Perception smoke: stub backends produce expected labels |
| `test_logger.py` | ~100 | unit | AuditLogger: session lifecycle, log_event, update_stm32_ack, flag_event, schema constraints |
| `test_dashboard.py` | ~40 | unit | Dashboard API endpoints, read-only enforcement |
| `test_smoke.py` (audit) | ~8 | smoke | Audit service end-to-end |

### Key test patterns

**Timeout path (no peer response)** — uses an OS pipe write-end only. The governance filter can write frames but never receives a response:

```python
rfd, wfd = os.pipe()
wfile = open(wfd, "wb", buffering=0)
gov = GovernanceFilter(..., channel=wfile, response_timeout_s=0.05)
gov.process_frame([det(confidence=0.91)])
# stm32_ack is NULL in audit_log — timeout confirmed
os.close(rfd); wfile.close()
```

**Float32 rounding regression** — verifies the dual-gate edge case is caught:

```python
gov.process_frame([det(confidence=0.70)])
row = log_row(audit_logger)
assert row["command_sent"] == 1   # Linux gate passed it
assert row["stm32_ack"] == 0     # MCU gate caught the float32 rounding
```

**Kill-switch reject** — uses `MockSTM32H5.trigger_kill_switch()`:

```python
peer.trigger_kill_switch()
time.sleep(0.05)  # allow mock reader thread to process state change
gov.process_frame([det(confidence=0.91)])
assert row["command_sent"] == 1   # Linux sent it
assert row["stm32_ack"] == 0     # MCU rejected (KILL_SWITCH_ACTIVE)
```

---

## 11. Planned Extensions (Steps 7–8)

### Step 7 — Alvik firmware

**Goal:** make the Alvik respond to `CommandRequest` frames over USB-C serial, execute motor commands, and return `CommandAck` or `CommandReject`.

**Responsibility split:**

| Processor | Responsibility |
|---|---|
| STM32F411 | Receive and validate `CommandRequest`, check kill-switch GPIO, execute motor commands, return `CommandAck` / `CommandReject` |
| ESP32-S3 | USB-C serial bridge to STM32F411; optionally Wi-Fi telemetry |

**Open decisions:**
- Language: MicroPython or Arduino C for STM32F411 firmware?
- Transport: USB-C serial or Bluetooth 5.1 for `CommandRequest` reception?
- Motor command mapping: `ActionType.HALT` → stop all motors; `ActionType.MOVE_JOINT_1..6` → mapped to left/right motor differential drive; `ActionType.GRIPPER_OPEN/CLOSE` → not yet mapped (Alvik has no gripper; may map to forward/backward or ignored)

**Governance constraint:** the Alvik STM32F411 must enforce the same two checks as all other STM32 implementations: `audit_ref ≠ 0` and independent float32 confidence gate. A third `RejectReason.AUDIT_REF_ZERO` response confirms the protocol invariant survives to Tier 3.

### Step 8 — UNO Q 4GB perception service

**Goal:** replace the stub backends with real camera-driven inference on the UNO Q 4GB, and send `DetectionResult` objects to the VENTUNO Q governance filter over the network.

**Architecture:**

```
UNO Q 4GB (QRB2210 Linux):
  Camera (ISP) → OpenCV capture → YOLO-X / MediaPipe / PoseNet
  → DetectionResult serialised (JSON or protobuf)
  → HTTP/gRPC or UDP multicast to VENTUNO Q

VENTUNO Q (IQ8 NPU Linux):
  Network receiver → list[DetectionResult]
  → GovernanceFilter.process_frame()   [unchanged from current implementation]
```

**Open decisions:**
- Camera module: Arduino native CSI module (if available) or Arducam / Waveshare MIPI module?
- Network transport: gRPC (reliable, typed), UDP (low latency, best-effort), or USB-C UART (no Wi-Fi dependency)?
- Model runtime: Qualcomm AI Hub on VENTUNO Q for heavy models; QRB2210 onboard GPU/NPU for pre-processing?

---

## 12. Open Decisions

| Decision | Options | Current default |
|---|---|---|
| UNO Q 4GB camera module | Arduino native / Arducam MIPI / Waveshare | Unknown — pending store research |
| UNO Q 4GB ↔ VENTUNO Q transport | Wi-Fi UDP / gRPC / USB-C UART | Wi-Fi (to be validated) |
| Alvik firmware language | MicroPython / Arduino C | TBD |
| Alvik IPC reception transport | USB-C serial / Bluetooth 5.1 | USB-C (simpler, deterministic) |
| Alvik motor mapping for MOVE_JOINT_1..6 | Differential drive mapping / ignored | TBD |
| Alvik GRIPPER_OPEN/CLOSE mapping | Forward/backward / ignored / extension gripper | TBD |
| Confidence threshold calibration | 0.70 (current) / data-driven from HRC injury studies | 0.70 (engineering default) |
| Audit log tamper-proofing | SQLite WAL (current, tamper-evident) / signed entries / TPM | SQLite WAL |
| Audit database location | Local SQLite / dedicated NVMe / remote syslog | Local SQLite |
| Dashboard authentication | None (local-only) / mTLS / token | None (demonstrator) |
