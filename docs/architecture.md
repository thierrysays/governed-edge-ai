# governed-edge-ai: Architecture & Functional Specification

**Version:** 2.0, 2026-08-19
**Status:** Steps 1 to 9 fully implemented and tested. Four boards, two links, 611 tests, 100% line coverage.

**What changed in 2.0:** the addition of an Arduino UNO R4 WiFi as an independent oversight node (Tier 0), and everything that followed from it: a second IPC link, an audit attestation hash chain, two new governance invariants, a new `oversight` actor in the audit schema, and a C++ firmware whose state machine is held to a Python reference model by a parity test suite.

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
   - 4.7 Audit Attestation
   - 4.8 SupervisorLink
   - 4.9 MockR4Supervisor
   - 4.10 R4 Supervisor Firmware
5. [IPC Protocol Specification](#5-ipc-protocol-specification)
6. [Audit Data Model](#6-audit-data-model)
7. [Governance Contract](#7-governance-contract)
8. [Sequence Diagrams](#8-sequence-diagrams)
9. [Configuration & Deployment](#9-configuration--deployment)
10. [Test Architecture](#10-test-architecture)
11. [Completed Implementation (Steps 7 to 9)](#11-completed-implementation-steps-7-to-9)
12. [Threat Model and Limits](#12-threat-model-and-limits)
13. [Remaining Open Decisions](#13-remaining-open-decisions)

---

## 1. System Overview

**governed-edge-ai** demonstrates that AI governance invariants can be enforced at the hardware and protocol level: not merely documented as policy. The central claim: in a physical AI system, no actuator command should be executable without a prior, confirmed audit log entry, and the function that supervises that rule must not depend on the system it supervises. This project implements and proves both claims across four physical boards.

### Core invariants

```
Invariant A, log before act:
  ∀ actuation: ∃ audit_ref ≥ 1  such that  log_event(audit_ref) precedes  transmit(CommandRequest)

Invariant B, witness before act:
  ∀ actuation: publish(digest_n) precedes transmit(CommandRequest)
  where digest_n = SHA-256(digest_{n-1} ‖ canonical(row_n)) and the digest is
  retained on a board outside the governance host

Invariant C, oversight is not revocable by its subject:
  ∄ message m in the protocol such that governance_tier.send(m) clears an override
```

The STM32 co-processor on the actuation side enforces Invariant A by rejecting any `CommandRequest` frame where `audit_ref == 0`. The UNO R4 WiFi oversight node enforces B by retaining digests off-host, and C by exposing no inbound message that releases its latch. All three constraints are structural, not procedural.

### Why a fourth board

Version 1.0 had a defensible weakness worth naming. The human override lived inside the system it was meant to override: the gesture HALT travelled through the AI perception pipeline, and the kill switch sat on the actuation MCU that the governance node itself commands. Both were real controls, and both depended on the health of the thing they supervised. A compromised or hung governance host took its own oversight down with it.

The UNO R4 WiFi is the answer to that. It is not on the command path, it is reached by its own link, its enforcement does not depend on that link, and no message the governance tier can send releases it. In audit language it is a third line of defence rather than a second control in the first line.

### What is implemented

| Step | Module | Description |
|---|---|---|
| 1 | `audit-service/logger.py` | Append-only SQLite audit logger |
| 2 | `linux-stack/ipc/codec.py` | Binary IPC protocol codec |
| 3 | `audit-service/dashboard/` | Read-only audit dashboard |
| 4 | `linux-stack/ipc/mock_peer.py` | Hardware simulator (pty-based) |
| 5 | `linux-stack/perception/` | Perception pipeline interface and stub backends |
| 6 | `linux-stack/governance/filter.py` | Governance filter (the safety gate) |
| 7 | `alvik-firmware/` | Alvik firmware: four governance gates, motor mapping, kill-switch GPIO |
| 8 | `linux-stack/perception/uno_q_service.py` | UNO Q perception service: camera, models, TCP transport |
| 9 | `linux-stack/oversight/`, `r4-supervisor/` | Oversight tier: attestation chain, supervisor link, R4 firmware |

---

## 2. Hardware Architecture

### Boards

| Board | Processor A | Processor B | Key peripherals | Role |
|---|---|---|---|---|
| **Arduino UNO Q 4GB** | Qualcomm QRB2210 (quad-core Cortex-A53, 2 GHz) | STM32U585 (Cortex-M33, 160 MHz) | Dual ISP 13 MP / 30 fps, 4 GB LPDDR4, 32 GB eMMC, Wi-Fi 5, BT 5.1 | Perception node |
| **Arduino VENTUNO Q** | Qualcomm IQ-8275 NPU (40 TOPS, Cortex-A55) | STM32H5 (Cortex-M33, 250 MHz) | 16 GB LPDDR5, Wi-Fi 6E, BT 5.3 | Governance brain |
| **Arduino Alvik** | ESP32-S3 (Xtensa LX7, 240 MHz) | STM32F411RC (Cortex-M4, 100 MHz) | ToF 8×8 (VL53L7CX, 350 cm), IMU 6-axis (LSM6DSOX), colour (APDS-9660), 18650 battery, LEGO connector | Physical body |
| **Arduino UNO R4 WiFi** | Renesas RA4M1 (Cortex-M4, 48 MHz) | ESP32-S3 (Wi-Fi and BLE co-processor) | 12x8 LED matrix, 32 KB SRAM, 256 KB flash, Qwiic, DAC, CAN | Oversight node |

All four boards are owned. No external actuator required beyond Alvik's built-in drivetrain. The R4 needs two momentary buttons and four jumper wires; `docs/deployment-guide.md` Part 1 has the list.

### Why the R4 is the right board for this role

Not because it is the most capable board on the bench. Because it is the least.

| Property | Why it matters here |
|---|---|
| 48 MHz, 32 KB SRAM, no OS | The whole firmware is a few hundred lines of C++ with no scheduler, no filesystem and no network stack in the default build. Small enough to read in one sitting, which is what an oversight function should be. |
| 12x8 LED matrix on board | Governance state is legible from across a room without a screen or a laptop. |
| Native USB-C serial | Same link discipline as the other two, no extra adapter. |
| GPIO with nothing else on it | D3 drives the Alvik kill-switch input directly. No bus arbitration, no driver, no software in the path. |
| Wi-Fi available but optional | A remote console exists and is off by default. An oversight node with fewer network surfaces is a better oversight node. |

An oversight function running on the most powerful board in the system would be the wrong shape. The argument here is that the supervisor should be simpler than the supervised, so that its correctness is checkable by inspection.

### Physical connectivity

```
┌─────────────────────┐      TCP over Wi-Fi     ┌──────────────────────┐
│    UNO Q 4GB        │ ─────────────────────►  │    VENTUNO Q         │
│  Perception Node    │   DetectionResult JSON   │  Governance Brain    │
│  QRB2210 + STM32U5  │        port 9100         │  IQ8 NPU + STM32H5   │
│  Dual ISP cameras   │                          │  AuditLogger SQLite  │
└─────────────────────┘                          └───┬──────────────┬───┘
                                                     │              │
                              SUPERVISOR_HEARTBEAT   │              │  USB-C / UART
                              ATTEST_DIGEST          │              │  CommandRequest
                                    USB-C            │              │
                                                     ▼              ▼
                              ┌──────────────────────────┐  ┌──────────────────────┐
                              │    UNO R4 WiFi           │  │    Alvik             │
                              │  Oversight Node          │  │  Physical Body       │
                              │  RA4M1 + ESP32-S3        │  │  ESP32-S3 + STM32F411│
                              │  Button, 12x8 matrix     │  │  Motors + ToF + IMU  │
                              │  64 retained digests     │  │  Kill-switch pin D4  │
                              └───────────┬──────────────┘  └──────────▲───────────┘
                                          │                            │
                                          └────────────────────────────┘
                                            D3 hard kill line, GPIO
                                            no software in the path
```

**UNO Q to VENTUNO Q:** length-prefixed JSON over TCP, port 9100.

**VENTUNO Q to Alvik:** USB-C serial. The Alvik's USB-C port presents as a serial device to the host.

**VENTUNO Q to UNO R4 WiFi:** USB-C serial, the same binary frame format as the Alvik link with a different message set.

**UNO R4 WiFi to Alvik:** a GPIO line, D3 to D4, active low, with a common ground. This is the only connection in the system that carries no protocol. It exists precisely so that one enforcement path survives the failure or compromise of every other one.

### The two enforcement paths

The oversight node can stop the rig two ways, and the difference is the whole reason it is wired to two boards.

| Path | Mechanism | Survives |
|---|---|---|
| Soft veto | `OVERRIDE_ASSERT` over serial; the `GovernanceFilter` stops transmitting | A hung or misbehaving perception tier |
| Hard line | D3 into the Alvik kill-switch input; the Alvik firmware refuses every command | A compromised governance host, a cut oversight cable, a forged `OVERRIDE_CLEAR` |

The soft veto's trust boundary is the physical USB-C cable, and anyone who can write to that cable can forge a clear. This is tested rather than assumed, in `TestSerialLinkTrustBoundary`. The hard line is not reachable from any link.

---

## 3. Four-Tier Software Architecture

Tier 0 is numbered zero deliberately. It is not the first step in the pipeline; it sits beside the pipeline and watches it.

```
┌──────────────────────────────────────────────────────────────────────┐
│  TIER 0: OVERSIGHT  (UNO R4 WiFi, Renesas RA4M1)                    │
│                                                                      │
│  Inbound from Tier 2:   SUPERVISOR_HEARTBEAT   every 500 ms          │
│                         ATTEST_DIGEST          one per audited row   │
│                                                                      │
│  SupervisorState                                                     │
│    ├─ heartbeat watchdog: silence > 2 s  ──►  latch override         │
│    ├─ digest monotonicity: gap or rollback ──►  latch override       │
│    ├─ physical NC button                  ──►  latch override        │
│    └─ retained digest ring (64 entries, off the governance host)     │
│                                                                      │
│  Outbound to Tier 2:    OVERRIDE_ASSERT / OVERRIDE_CLEAR / ATTEST_ACK│
│  Outbound to Tier 3:    D3 kill line, GPIO, no protocol              │
│  Operator surface:      12x8 LED matrix, clear button                │
│                                                                      │
│  Accepts no instruction from Tier 2. Releasing an override is a      │
│  physical act at this board.                                         │
└───────────▲──────────────────────────────────────────────┬───────────┘
            │ heartbeat + digests                          │ veto + kill line
            │                                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  TIER 1: PERCEPTION  (UNO Q 4GB, Qualcomm QRB2210 Linux)           │
│                                                                      │
│  Camera (ISP)  ──►  PerceptionPipeline  ──►  DetectionResult[]      │
│   YOLO-X (object)    MediaPipe (gesture)    PoseNet (pose)           │
│                                                                      │
│  Output: list[DetectionResult] sent to Tier 2 over network/serial   │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │  DetectionResult[]
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  TIER 2: GOVERNANCE  (VENTUNO Q, Qualcomm IQ8 NPU + STM32H5)       │
│                                                                      │
│  GovernanceFilter                                                    │
│    ├─ SupervisorLink.poll()  ──►  override? suppress the whole frame │
│    ├─ sort by confidence (desc)                                      │
│    ├─ for each detection:                                            │
│    │   ├─ AuditLogger.log_event()  ──►  audit_ref ≥ 1              │
│    │   ├─ AuditLogger.fetch_event() ──► chain.append() ──► digest    │
│    │   ├─ SupervisorLink.record()  ──►  publish digest to Tier 0     │
│    │   └─ if highest & above threshold & no override:               │
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
│  TIER 3: ACTUATION  (Alvik, ESP32-S3 + STM32F411)                  │
│                                                                      │
│  STM32F411 receives CommandRequest                                   │
│    ├─ validate audit_ref ≠ 0                                         │
│    ├─ validate confidence ≥ threshold (float32 gate)                │
│    ├─ check kill-switch GPIO state (driven by Tier 0's D3 line)     │
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
  → SupervisorLink.poll() → override?                 [Tier 0 → Tier 2]
  → AuditEvent logged → audit_ref returned            [Tier 2]
  → fetch_event → chain.append → ATTEST_DIGEST        [Tier 2 → Tier 0]
  → CommandRequest(audit_ref, confidence, action)     [Tier 2 → Tier 3]
  → CommandAck | CommandReject(reason)                [Tier 3 → Tier 2]
  → update_stm32_ack(audit_ref, ack)                  [Tier 2]
```

The digest reaches Tier 0 before the command reaches Tier 3. That ordering is what makes the retained digests evidence rather than a log of a log.

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

Unix pty-based hardware simulator. Opened as a file descriptor: identical interface to a real UART device. Runs a reader thread that processes incoming frames, enforces the same state machine as the real firmware, and writes responses.

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

FAULT (any system error, terminal state until reset)
```

**Confidence gate:** threshold 0.70 in float32. The mock applies this independently of the Linux gate. Sending `confidence=0.70` (float64) encodes to slightly below 0.70 in float32 on the wire; the mock rejects it (`CONFIDENCE_BELOW_THRESHOLD`).

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

**`DetectionResult`**: frozen dataclass, the unit of perception output:

```python
@dataclass(frozen=True)
class DetectionResult:
    detection_type: str    # "object" | "gesture" | "pose"
    label: str             # e.g. "person", "thumbs_up", "proximity_breach"
    confidence: float      # clamped to [0.0, 1.0]

    def passes_threshold(self, threshold: float) -> bool: ...
```

**`PerceptionPipeline`**: ABC enforcing `run(frame) -> list[DetectionResult]`. Swap any backend without touching the governance layer.

**Stub backends (for testing without cameras or models):**

| Class | Label | Default confidence |
|---|---|---|
| `StubObjectDetector` | `person` / `object` | configurable |
| `StubGestureRecognizer` | `thumbs_up` | configurable |
| `StubPoseEstimator` | `proximity_breach` | configurable |
| `NullPipeline` | (none) | N/A |

**Planned real backends (Step 8):**

| Backend | Model | Detection types |
|---|---|---|
| YOLO-X (NPU-accelerated) | Qualcomm AI Hub runtime | object: person, robot_part, tool |
| MediaPipe | CPU or NPU | gesture: stop, thumbs_up, thumbs_down |
| PoseNet | NPU | pose: proximity_breach |

---

### 4.4 Governance Filter (`linux-stack/governance/filter.py`)

The central safety gate. Sits between the perception pipeline output and the IPC channel. See [Section 7](#7-governance-contract) for the full governance contract.

**Default command map** (safety-conservative: unknown labels default to HALT):

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
    supervisor: SupervisorLink | None = None,
)
```

**`process_frame(detections: list[DetectionResult]) → None`**

1. Return immediately if `detections` is empty.
2. Poll the oversight node once for the whole frame. An active override becomes a note recorded against every row in the frame, so the veto is on record against the same detections it suppressed.
3. Sort by `confidence` descending.
4. For each detection:
   a. Resolve `(action_type, action_param)` from command map.
   b. Determine `should_send`: True only for the first detection that passes the threshold, and only when no override is active.
   c. Call `logger.log_event(AuditEvent(..., command_sent=should_send, notes=override_note))` → `audit_ref`.
   d. Read the stored row back with `logger.fetch_event(audit_ref)`, fold it into the attestation chain, and publish the new head to the oversight node.
   e. If `should_send`: call `_send_command(detection, action_type, action_param, audit_ref)`.
   f. If response received: call `logger.update_stm32_ack(audit_ref, ack)`.

Step (d) reads the row back rather than reconstructing it from the `AuditEvent`. The chain must commit to what SQLite holds, not to what the process believes it wrote. The cost is one indexed `SELECT` per event.

**Transmit failure**: if `channel.write()` raises `OSError`, the row already says `command_sent=1` and the log is append-only, so the record cannot be withdrawn. The filter calls `flag_event()` with the error instead. An event marked for review is the honest way to record that a frame was composed and never reached the wire.

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
- `update_stm32_ack()` only writes if `stm32_ack IS NULL`, preventing overwriting a confirmed ACK.
- `flag_event()` uses `SET flag = 1`; cannot move 1 to 0.
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

### 4.7 Audit Attestation (`linux-stack/oversight/attestation.py`)

A rolling SHA-256 hash chain over `audit_log` rows.

**The problem it solves.** The audit log is append-only by construction of the write path, but that constraint lives on the same host as the governance service. Anyone who can reach the file can rewrite it, and nothing in the file itself would show that a row had changed. Append-only is a property of the writer, not of the bytes.

**The chain:**

```
digest_0 = GENESIS                                   (32 zero bytes)
digest_n = SHA-256( digest_{n-1} ‖ canonical(row_n) )
```

**`AuditRow`**: the columns the chain commits to.

| Committed | Excluded | Why |
|---|---|---|
| `id`, `ts`, `session_id`, `actor`, `detection_type`, `detection_label`, `confidence`, `command`, `command_sent` | `stm32_ack`, `flag`, `notes` | The excluded three are written after the row is created. Committing to them would break the chain on every legitimate update. The chain covers what the row asserted at the moment the command decision was taken. |

`canonical()` joins the fields with `0x1F` (unit separator) and renders `confidence` with `%.17g`, the shortest form that round-trips a float64 exactly. The separator choice is tested: moving a character across a field boundary must change the digest.

**`AuditChain`**: incremental, rejects a reference that does not increase. An out-of-order or repeated `audit_ref` is evidence, not something to absorb silently.

**`verify_database(conn, retained=...)`**: the offline reconciliation. Recomputes the chain from SQLite and compares it against the digests read back from the oversight node.

| Tampering | Detected by |
|---|---|
| A row edited in place | Recomputed head differs from the retained digest at that `audit_ref` |
| A row deleted from the middle | `find_gap()`: AUTOINCREMENT never reuses a rowid, so ids must run 1..N |
| The last row deleted | Only by the witness: the ids stay contiguous, but the node holds a digest for a row the database no longer contains |
| A deleted row reinserted verbatim | The reinsertion gets a new id, so the gap remains |

**What it is not.** The chain is unkeyed. An attacker who controls the governance host and the link to the R4 can forge a consistent chain over falsified rows going forward. It detects tampering with rows already witnessed, which is the property the audit argument needs. Signing the digests with a key held only by the R4 is on the open-decisions list.

---

### 4.8 SupervisorLink (`linux-stack/oversight/supervisor_link.py`)

The VENTUNO Q side of the oversight link.

| Method | Direction | Purpose |
|---|---|---|
| `poll() → bool` | inbound, non-blocking | Drain frames, update override state, return whether a veto is active |
| `record(row, command_sent=) → bytes` | outbound | Fold a row into the chain, publish the head, heartbeat if due |
| `heartbeat(force=False) → bool` | outbound | Rate-limited SUPERVISOR_HEARTBEAT |
| `override_active`, `override_reason`, `last_verdict`, `chain_head`, `link_alive` | state | Read-only |

**Fail-closed on link loss.** `fail_closed` defaults to True. If nothing is heard from the R4 for `link_timeout_s` (3 s), the link reports an override with reason `GOVERNANCE_HEARTBEAT_LOST`. A supervisor that cannot be reached is not evidence that oversight is satisfied. `--oversight-optional` disables this for bench work, and the service warns when it is used.

**Transport failures are swallowed.** The oversight node going dark must never take the governance service down with it, so writes are wrapped and errors dropped. `poll()` converts the resulting silence into an override on the next frame, which is the correct place for that decision to be taken.

**Local chain faults.** If the governance tier tries to publish a row that does not follow the chain, that is an attestation fault detected on this side rather than at the node. The link raises the override locally and publishes nothing: forwarding a digest that does not follow would corrupt the witness's record of what it saw.

---

### 4.9 MockR4Supervisor (`linux-stack/oversight/mock_supervisor.py`)

Pty-based model of the oversight node, following the same pattern as `MockSTM32H5`. It is the executable specification for the C++ firmware, not merely a test double.

**State machine:**

```
    WATCHING  --button press-------------------->  OVERRIDE
    WATCHING  --heartbeat older than 2 s-------->  OVERRIDE
    WATCHING  --digest gap or rollback---------->  OVERRIDE
    OVERRIDE  --clear_override(), only when the button is released
                and a fresh heartbeat has arrived------------>  WATCHING
```

The override latches. It does not lapse when its cause goes away, and no inbound message clears it.

| Property | Behaviour |
|---|---|
| `kill_line_asserted` | True while an override is latched, and also before the first heartbeat ever arrives. The board comes up holding the line, releases it on first contact, and needs no arming step. |
| `annunciator` | `WATCHING`, `OVERRIDE`, `STALE` or `ATTEST`: the 12x8 matrix glyph |
| `retained_digests` | Ring of 64 `(audit_ref, digest)` pairs, oldest evicted first. Models the R4's limited memory. |
| First reason wins | A later trigger does not relabel the record of what actually stopped the rig |

**Clearing an attestation override** resynchronises the expected reference to whatever the governance tier last reported. Otherwise every later digest would gap against a stale expectation and the node could never resume. The gap stays in the retained digests: the clear is the record that an operator looked at it and accepted it.

---

### 4.10 R4 Supervisor Firmware (`r4-supervisor/`)

| File | Contents |
|---|---|
| `r4_supervisor.ino` | Pins, LED matrix glyphs, serial I/O, optional Wi-Fi console |
| `supervisor_state.h` / `.cpp` | The state machine. No Arduino headers: pure logic, host-compilable. |
| `ipc_frame.h` / `.cpp` | IPC codec, oversight subset only |
| `test/parity_harness.cpp` | Host driver exposing the two logic files over a line protocol |

**No `COMMAND_REQUEST` decoder exists on this board.** It is not on the actuation path, and the absence is deliberate.

**How correctness is established.** The sketch cannot run in CI, but everything that decides behaviour is plain C++ with no Arduino headers. `linux-stack/tests/test_r4_firmware_parity.py` compiles those two files for the host with `-Wall -Wextra -Werror` and checks them against the Python reference model: byte-identical frames, identical verdict sequences, identical state transitions, identical constants. Two implementations of one state machine drift unless something checks them.

What that does not cover: the Arduino layer itself. Pin behaviour, the LED matrix driver, Wi-Fi, and `Serial` timing at 921600 baud need the board. See section 12.

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
| `SupervisorHeartbeat` | `0x30` | `uint64 last_audit_ref, uint8 system_state, uint32 events_logged, uint32 commands_sent` | 17 bytes |
| `AttestDigest` | `0x31` | `uint64 audit_ref, 32-byte SHA-256 chain head` | 40 bytes |
| `OverrideAssert` | `0xA0` | `uint64 timestamp_us, uint8 reason` | 9 bytes |
| `OverrideClear` | `0xA1` | `uint64 timestamp_us` | 8 bytes |
| `AttestAck` | `0xA2` | `uint64 audit_ref, uint8 verdict` | 9 bytes |

The last five belong to the oversight link. Both links share one frame format, one CRC and one parser; they differ in who may say what to whom. Full reference in `docs/ipc-protocol.md` v0.2.

### Critical field: `audit_ref`

- Encoded as `uint64` in the `CommandRequest` payload.
- Value `0` is reserved and rejected by the STM32 firmware with `RejectReason.AUDIT_REF_ZERO`.
- The Linux side obtains `audit_ref` from `AuditLogger.log_event()`, which returns a SQLite rowid (always ≥ 1). The value 0 is structurally impossible in normal operation.

### Frame length guard

A header claiming more bytes than the protocol allows cannot begin a valid frame, so `FrameParser` discards the magic byte and resynchronises. Without that guard the parser waits forever for bytes that never arrive, and every later frame is swallowed with them: one corrupt or hostile length field would take a link down until restart. The C++ port applies the same rule against `IPC_MAX_FRAME`.

This was found by the adversarial test suite rather than by review, and is covered by `TestOversizedLengthGuard`.

### Confidence float32 edge case

The `confidence` field is encoded as IEEE 754 float32 on the wire. The value `0.70` in Python (float64) is approximately `0.6999999999999999555910790149937383830547332763671875`. Encoded as float32, it becomes `0.699999988079071044921875`: slightly below `0.70`. The STM32 dual-layer gate, operating on the float32 value, rejects this as `CONFIDENCE_BELOW_THRESHOLD` even if the Linux gate passed it. This is the intended defence-in-depth behaviour, and is covered by a regression test (`test_low_confidence_float32_round_trip_rejected_by_stm32`).

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
| `actor` | TEXT | `'ai'` \| `'human_override'` \| `'oversight'` | Who initiated the event |
| `detection_type` | TEXT | `'object'` \| `'gesture'` \| `'pose'` \| `'oversight'` | Perception modality, or an oversight action |
| `detection_label` | TEXT | NOT NULL | e.g. `person`, `thumbs_up` |
| `confidence` | REAL | 0.0–1.0 | Model confidence score |
| `command` | TEXT | NOT NULL | Command name e.g. `HALT`, `GRIPPER_OPEN` |
| `command_sent` | INTEGER | 0 \| 1 | Whether the command was transmitted |
| `stm32_ack` | INTEGER (nullable) | 0 \| 1 \| NULL | MCU response: 1=ACK, 0=REJECT, NULL=no response |
| `flag` | INTEGER | 0 \| 1, default 0 | Human review flag (one-way: 0→1 only) |
| `notes` | TEXT (nullable) | N/A | Annotations |

### Indexes

| Index | Columns | Purpose |
|---|---|---|
| `idx_audit_ts` | `ts` | Time-range queries |
| `idx_audit_actor` | `actor` | Actor-based filtering |
| `idx_audit_flag` | `flag WHERE flag=1` | Partial index for flagged events |

### The `oversight` actor

Added with the R4. It distinguishes machine-initiated action by the supervisor from the two existing actors.

| Actor | Means |
|---|---|
| `ai` | Inference-initiated, from the perception pipeline |
| `human_override` | Operator action, including the R4's physical override button |
| `oversight` | Machine-initiated by the R4: governance heartbeat lost, attestation mismatch |

The distinction matters to an auditor reading the log after an incident. "A person stopped this" and "the supervisor stopped this because the governance tier went quiet" are different findings.

**Migration note.** `schema.sql` uses `CREATE TABLE IF NOT EXISTS`, so a database created before this change keeps the old CHECK constraints, and SQLite cannot alter a CHECK in place. Rebuilding is the path: create the new table under a temporary name, `INSERT ... SELECT` the existing rows, drop, rename. In the demonstrator a session is one power cycle, so in practice a new session starts a new file.

### Forensic semantics of `stm32_ack`

| Value | Meaning |
|---|---|
| `NULL` | `command_sent=0`: command was suppressed (below threshold or not highest confidence). `command_sent=1`: command was sent but no response arrived before `response_timeout_s`. Both are forensically distinct from a confirmed rejection. |
| `0` (False) | MCU received the command and explicitly rejected it (kill switch, confidence gate, malformed frame, etc.) |
| `1` (True) | MCU received the command and acknowledged execution |

### Two ways a command can be absent

A reader of the log should be able to tell these apart, and can:

| `command_sent` | `flag` | `notes` | Meaning |
|---|---|---|---|
| 0 | 0 | NULL | Suppressed by the confidence gate or the one-command-per-frame rule |
| 0 | 0 | `suppressed: oversight override active (...)` | Vetoed by the oversight node, with the reason |
| 1 | 1 | `transmit failed: ...` | Composed and never reached the wire |
| 1 | 0 | NULL, `stm32_ack` NULL | Sent, no response before the timeout |

---

## 7. Governance Contract

Eight invariants. Six are enforced by `GovernanceFilter`, two by the oversight tier. All are structural, not procedural.

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

`should_send` is `False` for any detection where `detection.passes_threshold(self._threshold)` returns `False`. Suppressed detections are still logged with `command_sent=False`; the suppression decision is on record for forensic analysis.

### Invariant 4: One command per frame

`command_sent_this_frame` is a boolean that flips to `True` after the first detection is transmitted. All subsequent detections in the same frame are logged with `command_sent=False` regardless of their confidence score.

### Invariant 5: Dual-layer confidence gate

The Linux gate operates on float64 values. The STM32H5 gate operates on float32 values received on the wire. They are independent: a detection can pass the Linux gate and be rejected by the MCU gate (as in the float32 rounding edge case at exactly 0.70). Neither gate can be bypassed by the other side.

### Invariant 6: ACK/REJECT tracking

`update_stm32_ack()` is called at most once per transmitted command, after `_read_response()` returns. If `_read_response()` returns `None` (timeout or I/O error), `update_stm32_ack()` is not called and `stm32_ack` remains `NULL` in the audit log. The `NULL` state is forensically meaningful and distinguishable from a confirmed rejection.

A write that fails outright is treated differently: the row already claims `command_sent=1` and the log is append-only, so the filter flags the event rather than leaving a record that reads as a command sent.

### Invariant 7: Oversight veto

When a `SupervisorLink` is attached, the oversight node is polled before any command is transmitted. While its override is asserted, every detection in the frame is logged with `command_sent=False` and the reason recorded in `notes`. The veto is one-directional: the filter can neither clear the override nor tell the oversight node to stand down, because the protocol contains no message that would do either.

The veto stops the command. It never stops the record: the system continues to log everything it sees while it is stopped, which is what makes the override reviewable afterwards.

### Invariant 8: Witness before act

Each logged row is read back from SQLite, folded into the attestation hash chain, and the new chain head published to the oversight node before the `CommandRequest` frame is written. The independent witness therefore holds a digest covering the audit row before the actuator ever sees the command.

**A limit worth stating.** The filter does not block on the node's `ATTEST_ACK` before transmitting. A verdict of `GAP` or `CHAIN_BREAK` raises the override and stops the *next* command, not the one in flight. Blocking on the acknowledgement would put the oversight link inside the actuation latency budget, which is the wrong trade for a link whose job is to observe. Whether that trade is correct is on the open-decisions list.

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

### 8.5 Oversight veto flow

```
Perception   GovernanceFilter   SupervisorLink   R4 node      AuditLogger   Alvik
──────────   ────────────────   ──────────────   ─────────    ───────────   ─────
                                                     │
                                          [operator presses the button]
                                                     │
                                   OVERRIDE_ASSERT   │
                                  ◄──────────────────│  (and D3 kill line asserts)
     │             │                    │            │
     │ Detection   │                    │            │
     │  0.99       │                    │            │
     │────────────►│                    │            │
     │             │ poll()             │            │
     │             │───────────────────►│            │
     │             │  True, OPERATOR_BUTTON          │
     │             │◄───────────────────│            │
     │             │                                 │
     │             │ log_event(command_sent=False,               │
     │             │   notes="suppressed: oversight override ...")│
     │             │────────────────────────────────────────────►│
     │             │                                 audit_ref=51│
     │             │◄────────────────────────────────────────────│
     │             │ record(row) → ATTEST_DIGEST     │
     │             │───────────────────►│───────────►│
     │             │                                 │
     │      [NO CommandRequest transmitted]                      │
     │      [the Alvik would refuse it anyway: D3 is held]       │
```

### 8.6 Attestation gap flow

```
GovernanceFilter    SupervisorLink        R4 node
────────────────    ──────────────        ───────
      │ record(row 1)     │                  │
      │──────────────────►│ ATTEST_DIGEST(1) │
      │                   │─────────────────►│  last_ref 0 → 1, CHAIN_OK
      │                   │◄─────────────────│  ATTEST_ACK(1, CHAIN_OK)
      │                   │                  │
   [rows 2 and 3 written outside the governed path: never published]
      │                   │                  │
      │ record(row 4)     │                  │
      │──────────────────►│ ATTEST_DIGEST(4) │
      │                   │─────────────────►│  4 > 1+1 → GAP
      │                   │◄─────────────────│  ATTEST_ACK(4, GAP)
      │                   │◄─────────────────│  OVERRIDE_ASSERT(ATTESTATION_MISMATCH)
      │                   │                  │  annunciator → ATTEST, D3 asserts
      │ poll() → True     │                  │
      │◄──────────────────│                  │
   [the next frame is suppressed; the one already in flight is not recalled]
```

### 8.7 Governance heartbeat lost

```
GovernanceFilter    SupervisorLink        R4 node                Alvik
────────────────    ──────────────        ───────                ─────
      │ heartbeat         │ SUPERVISOR_HEARTBEAT                    │
      │──────────────────►│─────────────────►│  watchdog re-armed   │
      │                   │                  │                      │
   [governance process crashes or hangs]     │                      │
      ╳                   │                  │                      │
                          │        2 s elapse, no heartbeat         │
                          │                  │  latch OVERRIDE      │
                          │                  │  annunciator → STALE │
                          │                  │  D3 kill line ───────►  every command
                          │◄─────────────────│  OVERRIDE_ASSERT        now refused
                          │                  │
   [restarting the service does not clear it: the override latched,
    and clearing is a physical act at the oversight node]
```

### 8.8 Timeout flow (no peer response)

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
make test         # Full test suite + coverage (includes the C++ parity tests)
make lint         # ruff check (security rules included)
make typecheck    # mypy strict (production files only, PYTHONPATH set)
make security     # bandit SAST + pip-audit CVE scan
make qa           # lint + typecheck + security + test  (CI gate)
```

Per-module variants: `make audit-test`, `make linux-test`, `make linux-lint`, etc. `make lint` also covers `alvik-firmware`. The R4 firmware has no Python linter; its gate is the parity suite, which compiles it with `-Werror`.

### Service invocation

```bash
# VENTUNO Q, governance tier
PYTHONPATH=/opt/governed-edge-ai/audit-service \
python3 -m governance.ventuno_q_service \
    --alvik /dev/alvik \
    --supervisor /dev/oversight \
    --db /data/audit.db
```

| Flag | Default | Meaning |
|---|---|---|
| `--supervisor` | `mock` | R4 serial device, `mock` for a pty-backed model, or `none` |
| `--oversight-optional` | off | Do not treat a lost oversight link as an override. Bench use only. |

`--supervisor none` runs the three-board configuration. The service logs a warning on that path: command dispatch has no independent veto and audit rows are not witnessed off-host. It is the arrangement the R4 was added to correct, not a supported deployment.

**A step-by-step guide, written for a first-time reader and starting from an unconfigured machine, is in `docs/deployment-guide.md`.** It covers the bill of materials, flashing both firmwares, wiring the kill line, systemd units, and a seven-test verification procedure for the governance controls.

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
- Sign the attestation digests with a key held only by the oversight node. The chain as built is tamper-evident against rows already witnessed; signing would extend that to rows written after a host compromise.
- Consider `synchronous=FULL` for stronger durability guarantees

### Reading the retained digests back

The offline reconciliation needs the digests the oversight node witnessed:

```python
import sqlite3
from oversight.attestation import verify_database

result = verify_database(sqlite3.connect("/data/audit.db"), retained=digests)
print(result.ok, result.reason)
```

Recovering `digests` from a running board currently means the Wi-Fi console, which is the least satisfying part of the design and is on the open-decisions list. In the test suite they come straight from `MockR4Supervisor.retained_digests`.

---

## 10. Test Architecture

### Overview

```
611 tests total · 100% line coverage on both modules · hardware-free
```

| Module | Tests | Coverage |
|---|---|---|
| linux-stack | 512 | 100% |
| audit-service | 99 | 100% |

All tests run without physical hardware. `MockSTM32H5` provides the actuation MCU via a Unix pty; `MockR4Supervisor` provides the oversight node the same way. Both are real implementations of their state machines rather than stubs, so the software path exercised in CI is the one that runs on the rig.

The coverage gate is `--cov-fail-under=98` on both modules. Entry-point guards (`if __name__ == "__main__":`) and one provably unreachable branch are excluded and marked; nothing else is.

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
| `test_smoke.py` (audit) | 4 | smoke | Audit service end-to-end |
| `test_codec_oversight.py` | 34 | unit, regression | The five oversight message types, wire sizes, CRC, length guards, the oversized-header regression |
| `test_attestation.py` | 33 | unit | Canonical rendering, chain mechanics, gap detection, tamper detection against a real database |
| `test_mock_supervisor.py` | 35 | unit | The R4 reference model: latch, watchdog, digest verdicts, annunciator, retained ring |
| `test_supervisor_link.py` | 30 | unit | The VENTUNO Q side: heartbeats, digests, override handling, fail-closed, framing |
| `test_oversight_governance.py` | 19 | integration | Invariants 7 and 8 end to end, against real ptys and a real database |
| `test_oversight_faults.py` | 23 | unit | Pulled cables, dead descriptors, chain faults, resynchronisation |
| `test_security_oversight.py` | 21 | security | Adversarial: four threat positions, see below |
| `test_r4_firmware_parity.py` | 42 | integration | The compiled C++ firmware against the Python reference model |
| `test_smoke_oversight.py` | 8 | smoke | Four-board path: accept, veto, reconcile, tamper, fail closed |
| `test_coverage_completion.py` | 24 | unit | Error and hardware-only branches the rest of the suite leaves untouched |
| `test_oversight.py` (audit) | 19 | unit | The `oversight` actor, and `fetch_event()` |
| `test_dashboard_db.py` (audit) | 8 | unit | The dashboard's real connection path, not the overridden one |

### Key test patterns

**Timeout path (no peer response)**: uses an OS pipe write-end only. The governance filter can write frames but never receives a response:

```python
rfd, wfd = os.pipe()
wfile = open(wfd, "wb", buffering=0)
gov = GovernanceFilter(..., channel=wfile, response_timeout_s=0.05)
gov.process_frame([det(confidence=0.91)])
# stm32_ack is NULL in audit_log: timeout confirmed
os.close(rfd); wfile.close()
```

**Float32 rounding regression**: verifies the dual-gate edge case is caught:

```python
gov.process_frame([det(confidence=0.70)])
row = log_row(audit_logger)
assert row["command_sent"] == 1   # Linux gate passed it
assert row["stm32_ack"] == 0     # MCU gate caught the float32 rounding
```

**Adversarial security testing** (`test_security_oversight.py`): four threat positions, each asserting both what holds and what does not.

| Position | Tested |
|---|---|
| T1, a compromised governance host | No message it can send clears an override. The whole outbound vocabulary is thrown at a latched node, including replayed R4 messages. |
| T2, an attacker on the oversight cable | A forged `OVERRIDE_CLEAR` *does* release the soft veto. The hard kill line is unaffected, and the Alvik still refuses. |
| T3, a compromised host with database write access | Edits, deletions, backdating and verbatim reinsertion are each detected against the retained digests. An unwitnessed database is shown to verify clean, which is why the digests live elsewhere. |
| T4, malformed or hostile input | Oversized length headers, random bytes, corrupt CRCs, SQL in a detection label, separator injection into the canonical row rendering. |

The T2 tests exist to document a limit rather than to demonstrate a strength. A control whose limits are undocumented is a control nobody can rely on.

**Firmware parity** (`test_r4_firmware_parity.py`): compiles `r4-supervisor/supervisor_state.cpp` and `ipc_frame.cpp` for the host with `-Wall -Wextra -Werror`, drives them through a line-protocol harness, and checks byte-identical frames, identical verdict sequences, identical state transitions and identical constants against the Python model. Skips with a stated reason when no compiler is present.

**Kill-switch reject**: uses `MockSTM32H5.trigger_kill_switch()`:

```python
peer.trigger_kill_switch()
time.sleep(0.05)  # allow mock reader thread to process state change
gov.process_frame([det(confidence=0.91)])
assert row["command_sent"] == 1   # Linux sent it
assert row["stm32_ack"] == 0     # MCU rejected (KILL_SWITCH_ACTIVE)
```

---

## 11. Completed Implementation (Steps 7 to 9)

All nine build steps are complete. This section records what was implemented and the decisions taken.

### Step 7: Alvik firmware (`alvik-firmware/`)

**Language chosen:** MicroPython (CPython-testable; no MicroPython-specific APIs used in the testable subset).

**Transport chosen:** USB-C serial (deterministic, no Wi-Fi dependency, native to Alvik's USB-C port).

**Motor mapping implemented** (`alvik-firmware/motor_map.py`):

| `ActionType` | Alvik motor command |
|---|---|
| `HALT` | `stop_motors()` |
| `MOVE_JOINT_1` | left wheel forward |
| `MOVE_JOINT_2` | right wheel forward |
| `MOVE_JOINT_3` | left wheel reverse |
| `MOVE_JOINT_4` | right wheel reverse |
| `MOVE_JOINT_5` | rotate left (differential) |
| `MOVE_JOINT_6` | rotate right (differential) |
| `GRIPPER_OPEN/CLOSE` | ignored (no gripper on Alvik) |

**Four firmware governance gates** (enforced in order in `alvik-firmware/main.py`):

1. `audit_ref != 0`: rejects `CommandRequest` with `audit_ref == 0`, returns `CommandReject(RejectReason.AUDIT_REF_ZERO)`
2. Kill-switch GPIO: if the kill-switch pin reads active, returns `CommandReject(RejectReason.KILL_SWITCH_ACTIVE)`
3. Float32 confidence gate: rejects if `confidence < 0.70` (independent of VENTUNO Q's Linux-side gate)
4. Known action type: rejects an unrecognised `ActionType`, returns `CommandReject(RejectReason.UNKNOWN_ACTION)`

**IPC codec subset** (`alvik-firmware/ipc_codec.py`): MicroPython-compatible binary codec, CRC-16/CCITT, all 8 message types, CPython-testable without MicroPython runtime.

### Step 8: UNO Q 4GB perception service (`linux-stack/perception/`)

**Camera integration:** V4L2 via OpenCV `VideoCapture(device, cv2.CAP_V4L2)`; `SyntheticFrameSource` used in CI (no hardware required).

**Network transport chosen:** length-prefixed JSON over TCP, port 9100. Each `DetectionResult` serialised as 4-byte big-endian uint32 (payload length) + UTF-8 JSON. VENTUNO Q listens as TCP server; UNO Q connects as client.

**Multi-backend fallback strategy** (`linux-stack/perception/uno_q_service.py`): `_build_backends()` attempts each production backend independently; catches `(ImportError, RuntimeError, OSError)` and falls back to a stub. `OSError` covers `FileNotFoundError` when model weights are installed but the `.task` file is absent.

**Backend stack:**

| Backend | Stub fallback | Detection type |
|---|---|---|
| `YOLOXBackend` | `StubObjectDetector` | Person / robot_part / tool |
| `MediaPipeBackend` | `StubGestureRecognizer` | Stop / thumbs_up/down / swipe |
| `PoseNetBackend` | `StubPoseEstimator` | Proximity boundary breach |

**Transport implementation** (`linux-stack/perception/network.py`): `DetectionResultServer` (VENTUNO Q side) and `DetectionResultClient` (UNO Q side), both using the same 4-byte length-prefix framing.

### Step 9: Oversight tier (`linux-stack/oversight/`, `r4-supervisor/`)

**Board chosen:** Arduino UNO R4 WiFi, for the reasons in section 2. The short version: the supervisor should be simpler than the supervised.

**Decisions taken:**

- **Two enforcement paths, not one.** A soft veto over serial and a hard GPIO line into the Alvik kill-switch input. The soft path is forgeable by anyone with the cable; the hard path is not reachable from any link. Building only the soft path would have produced a control with a single point of failure that a document would have described as independent.
- **The override latches.** It does not lapse when its cause goes away, and no inbound message clears it. Releasing one is a physical act at the board. This is the property that makes it oversight rather than a second gate.
- **Fail-closed by default on link loss.** Silence from the oversight node counts as an override. A supervisor that cannot be reached is not evidence that oversight is satisfied.
- **Kill line held from boot until the first heartbeat.** Not a latch, and no arming step: the line simply releases on first contact. A governance tier that has not yet said anything has not yet earned the authority to move a robot.
- **The chain commits to the stored row, not the intended one.** `fetch_event()` was added to the logger so the digest covers what SQLite holds. One indexed `SELECT` per event, and the alternative would have been a chain over the caller's beliefs.
- **`stm32_ack`, `flag` and `notes` excluded from the chain.** All three are written after the row is created, so committing to them would break the chain on every legitimate update.
- **A third audit actor, `oversight`.** Machine-initiated supervisor action reads differently from a human pressing a button, and an auditor after an incident needs to tell them apart. This changed a CHECK constraint; the migration note is in section 6.
- **The Python model is the specification, the C++ is the port.** Not the other way round. The model carries the test suite, and a parity harness holds the firmware to it.
- **Digest verdicts are limited to what the digest stream can prove.** The R4 stores digests, not rows, so it detects gaps and rollback live. Detecting an altered row is an offline reconciliation. Claiming more would have been a claim the board cannot support.
- **The filter does not block on `ATTEST_ACK`.** Discussed under Invariant 8. Open.

**Defects found while building this step**, both by the adversarial tests rather than by review:

| Defect | Consequence | Fix |
|---|---|---|
| `FrameParser` had no maximum-frame guard | One corrupt or hostile length header wedged a link permanently: every later frame was swallowed waiting for bytes that never came | Discard the magic byte and resynchronise when the length exceeds `MAX_PAYLOAD`. Regression tests in `TestOversizedLengthGuard`. |
| A failed `channel.write()` left a row reading `command_sent=1` | The audit log claimed a command was sent that never reached the wire | Catch `OSError` and `flag_event()` with the error. The log is append-only, so flagging is the only honest correction available. |

---

## 12. Threat Model and Limits

The governance claim is that authority is separated in circuitry rather than asserted in a document. That claim is worth only as much as the attempts made to break it, so this section states what holds, what does not, and what has not been tested at all.

### What holds

| Position | Outcome |
|---|---|
| A compromised governance host tries to silence its supervisor | No message in the protocol clears an override. Tested against the whole outbound vocabulary, including replayed R4 messages. |
| A compromised host rewrites the audit log | Edits, deletions, backdating and verbatim reinsertion are all detected against the retained digests. |
| The governance process crashes or hangs | The R4 watchdog latches within 2 s and asserts the kill line. Restarting the software does not clear it. |
| The oversight cable is cut | The governance tier fails closed within 3 s; the R4's hard line is unaffected. |
| Hostile bytes on either link | Length guards, CRC and resynchronisation. Tested with oversized headers, random bytes and corrupt frames. |
| SQL or separator injection through a detection label | Parameterised writes; the canonical rendering is separator-safe. Both tested. |

### What does not hold, and is documented rather than hidden

| Limit | Consequence |
|---|---|
| The oversight serial link is trusted to the extent the USB-C cable is | An attacker with the cable can forge `OVERRIDE_CLEAR` and release the soft veto. The hard kill line is unaffected, which is why it exists. |
| The attestation chain is unkeyed | A host controlling both the database and the link can forge a consistent chain over falsified rows *going forward*. Rows already witnessed remain protected. |
| The filter does not block on `ATTEST_ACK` | A `GAP` or `CHAIN_BREAK` verdict stops the next command, not the one in flight. |
| The R4 retains only 64 digests | A long session evicts the oldest. Reconciliation covers the window the node still holds. |
| Reading digests back needs the Wi-Fi console | The least satisfying part of the design. |
| The kill line depends on a shared ground | A floating line reads as noise, and this is the one failure the design cannot detect for itself. |

### What is not tested at all

Everything above is exercised in software. The following need the physical rig and have not been:

- Pin behaviour, debounce timing and the LED matrix driver on real hardware
- `Serial` throughput and framing at 921600 baud over a real USB-C link
- The Wi-Fi console
- Electrical behaviour of the kill line: rise time, noise immunity, ground-loop effects
- Every timing figure in the protocol specification. They are design targets, not measurements.

This is a first embedded build. The software claims are tested; the hardware claims are, for now, claims.

---

## 13. Remaining Open Decisions

| Decision | Options | Current default |
|---|---|---|
| UNO Q 4GB camera module | Arduino native CSI / Arducam MIPI / Waveshare | Pending: no Arduino-native CSI module confirmed available |
| Confidence threshold calibration | 0.70 (current) / data-driven from HRC injury studies | 0.70 (engineering default) |
| Attestation digest signing | Unkeyed chain (current) / HMAC with a key held only by the R4 / TPM | Unkeyed |
| Blocking on `ATTEST_ACK` before transmit | Non-blocking (current) / blocking with a deadline | Non-blocking |
| Retained digest read-back | Wi-Fi console (current) / a serial command / SD card | Wi-Fi console |
| Retained digest capacity | 64 in SRAM (current) / EEPROM-backed / external flash | 64 in SRAM |
| Audit database location | Local SQLite / dedicated NVMe / remote syslog | Local SQLite |
| Dashboard authentication | None (local-only) / mTLS / token | None (demonstrator) |
| Oversight override auto-clear | Never (current, requires a physical act) / timed / on operator confirmation over the console | Never |
