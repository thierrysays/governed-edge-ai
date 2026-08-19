# Governed Edge AI: Project Summary (All Eight Steps)

**Project:** governed-edge-ai Physical AI Demonstrator
**Repository:** https://github.com/thierrysays/governed-edge-ai (public, main branch)
**Last updated:** 19 August 2026
**Status:** Complete. All eight build steps shipped. 313 tests, 95%+ coverage per module, full QA suite green.

---

## What This Project Is

A working demonstrator of governance-first Physical AI across three Arduino boards. The core thesis: for physical AI systems, safety invariants must be enforced at the hardware and protocol level, not only in policy documents or governance frameworks.

No actuation occurs without a prior audit log entry. This is enforced at the IPC protocol layer, not in policy: the STM32H5 on the VENTUNO Q rejects any `CommandRequest` frame with `audit_ref == 0` before passing it downstream. The Alvik firmware enforces the same check independently.

**Stack:** Python 3.11, SQLite WAL-mode, binary IPC with CRC-16/CCITT, TCP JSON transport, MicroPython firmware, pytest, ruff, mypy, bandit, pip-audit. 313 tests across two modules, hardware-free test suite via pty-based co-processor mock and loopback TCP transport.

---

## Three-Board Architecture

```
UNO Q 4GB          VENTUNO Q              Alvik
────────────────   ────────────────────   ─────────────────────
Camera (ISP)  ──►  GovernanceFilter  ──►  STM32F411 (MicroPython)
YOLO-X             AuditLogger            Motors / ToF / IMU
MediaPipe          STM32H5 dual gate      CommandAck / CommandReject
PoseNet            audit_ref >= 1 enforced
Qualcomm QRB2210   Qualcomm IQ8 40 TOPS   ESP32-S3 + STM32F411
STM32U585          STM32H5
```

**All three boards are owned.** No external actuator hardware required beyond the Alvik's built-in motors.

| Board | Role | Key specs |
|---|---|---|
| Arduino UNO Q 4GB | Perception node | Qualcomm QRB2210, dual ISP 13 MP / 30 fps, STM32U585, 4 GB LPDDR4, Wi-Fi 5 |
| Arduino VENTUNO Q | Governance brain | Qualcomm IQ-8275 NPU 40 TOPS, STM32H5, 16 GB RAM |
| Arduino Alvik | Physical body | ESP32-S3 + STM32F411, wheeled robot, ToF 8x8, 6-axis IMU, line follower |

**Data flow:** UNO Q 4GB captures camera frames and runs multi-backend inference. DetectionResult objects are sent to VENTUNO Q over TCP (port 9100, length-prefixed JSON). GovernanceFilter logs to SQLite, gates on confidence, and transmits audited CommandRequest frames over IPC. VENTUNO Q STM32H5 validates audit_ref, enforces an independent confidence gate, and passes the command to Alvik via USB-C serial. Alvik STM32F411 enforces its own four-gate check, executes the motor command, and returns CommandAck or CommandReject.

---

## Governance Controls Implemented

| Control objective | Framework reference | Implementation |
|---|---|---|
| Log-before-act | ISO 42001 Clause 9.1 | `audit_ref` (SQLite rowid >= 1) obtained before any CommandRequest is transmitted |
| No actuation without audit | ISO 42001, NIST AI RMF GOVERN | STM32H5 rejects CommandRequest with `audit_ref == 0` at the protocol layer |
| Confidence gate (dual-layer) | Defence-in-depth | Linux gate at 0.70 float64; STM32H5 gate at 0.70 float32, independent enforcement |
| One command per frame | Segregation of duties | Highest-confidence detection only; all others logged as suppressed |
| Full suppression record | Auditability | Every detection logged regardless of whether a command was sent |
| ACK/REJECT tracking | ISO 42001 monitoring | `stm32_ack` column updated after MCU response; NULL on timeout (forensically meaningful) |
| Human override authority | ISO 42001, COBIT APO | Gesture-triggered HALT via perception pipeline; kill-switch state machine on STM32H5 |
| Alvik firmware gate | Defence-in-depth (Tier 3) | Four independent governance gates before any motor command executes |

---

## Build Sequence (Eight Steps)

### Step 1: Audit Logger (`audit-service/logger.py`)

Append-only SQLite audit service in WAL mode. The central artefact everything else depends on.

Key design:
- `open_session(board_serial)` returns a session UUID
- `log_event(AuditEvent)` writes and returns the SQLite rowid (the `audit_ref`; always >= 1)
- `update_stm32_ack(audit_ref, ack)` idempotent one-time update after MCU responds
- `flag_event(audit_ref, reason)` one-way flag for forensic marking; cannot be unflagged
- Schema: `sessions` table + `audit_log` table; `stm32_ack` column is NULL until the MCU responds
- WAL mode + `synchronous=NORMAL`: concurrent readers (dashboard) have safe access without blocking the write path

The `audit_ref` field in the IPC protocol was designed specifically to create a tamper-evident ordering: you cannot send a command without a corresponding log entry that predates it.

QA: 72 tests across logger, dashboard, and smoke suites. 96.13% coverage.

---

### Step 2: IPC Codec (`linux-stack/ipc/codec.py`)

Binary protocol between the Linux NPU and the STM32H5 co-processor. Eight message types:

| Message | Direction | Purpose |
|---|---|---|
| `CommandRequest` | Linux to MCU | Governance-approved action |
| `CommandAck` | MCU to Linux | Accepted and executed |
| `CommandReject` | MCU to Linux | Rejected (kill switch / confidence gate / audit_ref=0) |
| `Heartbeat` | Linux to MCU | Watchdog keepalive |
| `HeartbeatAck` | MCU to Linux | Watchdog acknowledged |
| `HaltNotify` | MCU to Linux | Emergency halt from MCU |
| `StatusQuery` | Linux to MCU | Request MCU state |
| `StatusResponse` | MCU to Linux | Current MCU state |

CRC-16/CCITT (IBM-3740 variant, poly=0x1021, init=0xFFFF) on every frame. `FrameParser` handles incremental stream input across multiple `read()` calls: garbage bytes before the magic byte are silently discarded; a single corrupt frame does not abort the stream.

Frame sizes: Heartbeat/HeartbeatAck/StatusQuery: 6 bytes total; CommandAck/CommandReject/HaltNotify: 15 bytes; StatusResponse: 20 bytes; CommandRequest: 26 bytes (max 261 bytes per spec).

Critical design: `audit_ref=0` is a reserved sentinel. The STM32H5 rejects it unconditionally at priority 1, before any other check.

---

### Step 3: Audit Dashboard (`audit-service/dashboard/`)

Flask/SQLAlchemy read-only dashboard over the audit log.

Routes:
- `GET /health`: liveness probe
- `GET /sessions`: all sessions, newest-first
- `GET /events`: filtered event log (session_id, actor, flagged, limit, offset)
- `POST /events/{id}/flag`: human-reviewer annotation (the only write path)

Intentionally read-only: the governance filter writes via `AuditLogger`; the dashboard only reads. WAL mode on every connection allows concurrent access without locking. Boolean coercion: SQLite 0/1 converted to `true`/`false` in JSON.

---

### Step 4: Mock STM32H5 Peer (`linux-stack/ipc/mock_peer.py`)

Unix pty-based hardware simulator. Behaves identically to the real co-processor: decodes `CommandRequest` frames, enforces its own confidence gate in float32, manages state machine (ARMED, BUSY, HALTED, FAULT), responds with `CommandAck` or `CommandReject`.

```python
with MockSTM32H5(watchdog_ms=10_000.0) as peer:
    ch = open(peer.device, "rb+", buffering=0)
    # ch is a binary r/w channel identical to a real UART
```

Reject gate priority order:
1. `audit_ref == 0` (AUDIT_REF_ZERO) -- unconditional, regardless of system state
2. Kill switch open (KILL_SWITCH_ACTIVE)
3. HALTED state (WATCHDOG_TIMEOUT)
4. FAULT state (SYSTEM_FAULT)
5. Confidence below float32 threshold (CONFIDENCE_BELOW_THRESHOLD)

Float32 boundary: `confidence=0.70` in float64 encodes to slightly below 0.70 in float32 and is correctly rejected by the mock. Tests use 0.75 (exactly representable) to exercise the accept path.

Enables the entire test suite to run without physical hardware.

---

### Step 5: Perception Pipeline (`linux-stack/perception/base.py`, `backends.py`, `capture.py`)

Typed detection layer. `PerceptionPipeline` ABC enforces `run(frame) -> list[DetectionResult]` across all backends.

```python
@dataclass(frozen=True)
class DetectionResult:
    detection_type: str    # "object" | "gesture" | "pose"
    label: str
    confidence: float      # clamped to [0.0, 0.9999]

    def passes_threshold(self, threshold: float) -> bool: ...
```

`confidence` clamped at 0.9999 (transport concern: IPC codec encodes as float32; softmax values can slightly exceed 1.0). Frozen dataclass: governance layer cannot mutate a detection after the backend produces it.

Stub backends for hardware-free CI:
- `StubObjectDetector(confidence)` returns `person` detection
- `StubGestureRecognizer(confidence)` returns `thumbs_up` detection
- `StubPoseEstimator(confidence)` returns `proximity_breach` detection
- `NullPipeline()` returns empty list (no detections, no command)

Camera capture (`capture.py`): `V4L2FrameSource` via OpenCV `VideoCapture(device, cv2.CAP_V4L2)`; `SyntheticFrameSource` for CI; `FileFrameSource` for replay.

Production backends (`backends_impl.py`): `YOLOXBackend`, `MediaPipeBackend`, `PoseNetBackend`. All implement the same ABC; the governance filter is backend-agnostic.

---

### Step 6: Governance Filter (`linux-stack/governance/filter.py`)

The safety gate between the perception pipeline and IPC dispatch. Enforces six invariants:

1. **Log-before-act**: `audit_ref` obtained from `logger.log_event()` before any frame is transmitted. If logging fails, exception propagates and no frame is sent.
2. **No log, no command**: structural, not conditional. The send is inside the block that follows the log call.
3. **Confidence gate (Linux side)**: detections below threshold logged with `command_sent=False`. Suppression is on record.
4. **One command per frame**: highest-confidence detection above threshold selected; all others suppressed.
5. **Dual-layer gate**: Linux gate and STM32H5 gate operate independently.
6. **ACK/REJECT tracking**: `update_stm32_ack()` called once per transmitted command. Timeout leaves `stm32_ack` NULL.

Default command map (safety-conservative; unknown labels default to HALT):

| Label | Command |
|---|---|
| `person`, `robot_part`, `tool` | HALT |
| `stop` (gesture) | HALT |
| `thumbs_up` (gesture) | GRIPPER_OPEN |
| `thumbs_down` (gesture) | GRIPPER_CLOSE |
| `swipe_left`, `swipe_right` (gesture) | HALT |
| `proximity_breach` (pose) | HALT |
| *(anything else)* | HALT |

Note: GRIPPER_OPEN and GRIPPER_CLOSE are valid IPC ActionTypes. Alvik firmware ignores them (Alvik has no gripper). HALT and MOVE_JOINT_1..6 are the actively executed commands on the Alvik.

VENTUNO Q governance service (`ventuno_q_service.py`): listens on TCP port 9100, receives `list[DetectionResult]` from UNO Q, passes to `GovernanceFilter`, dispatches audited `CommandRequest` to Alvik via USB-C serial.

---

### Step 7: Alvik Firmware (`alvik-firmware/`)

**Language:** MicroPython (CPython-testable; no MicroPython-specific APIs in the testable subset).

**Transport:** USB-C serial (deterministic, no Wi-Fi dependency, native to Alvik's USB-C port).

**Four firmware governance gates** (enforced in order, `alvik-firmware/main.py`):

1. `audit_ref != 0`: rejects CommandRequest with `audit_ref == 0`, returns `CommandReject(AUDIT_REF_ZERO)`
2. Kill-switch GPIO: if active, returns `CommandReject(KILL_SWITCH_ACTIVE)`
3. Float32 confidence gate: rejects if `confidence < 0.70`, independent of VENTUNO Q gate
4. Known action type: rejects unrecognised ActionType, returns `CommandReject(UNKNOWN_ACTION)`

**Motor map** (`alvik-firmware/motor_map.py`):

| ActionType | Alvik motor command |
|---|---|
| `HALT` | `stop_motors()` |
| `MOVE_JOINT_1` | left wheel forward |
| `MOVE_JOINT_2` | right wheel forward |
| `MOVE_JOINT_3` | left wheel reverse |
| `MOVE_JOINT_4` | right wheel reverse |
| `MOVE_JOINT_5` | rotate left (differential) |
| `MOVE_JOINT_6` | rotate right (differential) |
| `GRIPPER_OPEN/CLOSE` | ignored (no gripper on Alvik) |

**IPC codec subset** (`alvik-firmware/ipc_codec.py`): MicroPython-compatible binary codec, CRC-16/CCITT, all 8 message types, CPython-testable without MicroPython runtime.

---

### Step 8: UNO Q Perception Service (`linux-stack/perception/uno_q_service.py`, `network.py`)

**Camera integration:** V4L2 via OpenCV `VideoCapture(device, cv2.CAP_V4L2)`. `SyntheticFrameSource` used in CI.

**Network transport:** Length-prefixed JSON over TCP, port 9100. Each `DetectionResult` serialised as 4-byte big-endian uint32 (payload length) + UTF-8 JSON. VENTUNO Q listens as TCP server; UNO Q connects as client.

**Multi-backend fallback strategy:** `_build_backends()` attempts each production backend independently; catches `(ImportError, RuntimeError, OSError)` and falls back to a stub. `OSError` covers `FileNotFoundError` when model weights are installed but the `.task` file is absent.

| Backend | Stub fallback | Detection type |
|---|---|---|
| `YOLOXBackend` | `StubObjectDetector` | Person / robot_part / tool |
| `MediaPipeBackend` | `StubGestureRecognizer` | Stop / thumbs_up/down / swipe |
| `PoseNetBackend` | `StubPoseEstimator` | Proximity boundary breach |

**DetectionResult transport** (`network.py`): `DetectionResultServer` (VENTUNO Q side) and `DetectionResultClient` (UNO Q side) use the same 4-byte length-prefix framing. Loopback TCP used in CI tests.

---

## QA Baseline

| Module | Tests | Coverage |
|---|---|---|
| linux-stack | 241 | 95.76% |
| audit-service | 72 | 96.13% |
| **Total** | **313** | **95%+ per module** |

All checks pass: ruff, mypy, bandit (no issues), pip-audit (no CVEs).

```bash
make qa        # lint + typecheck + security + full test suite
make smoke     # fast sanity pass, hardware-free
```

All tests run without physical hardware. CI reproduces the full stack: synthetic camera frames, pty-based mock STM32H5, loopback TCP transport.

---

## Repository Structure

```
governed-edge-ai/
├── Makefile                       # make smoke | test | lint | typecheck | security | qa
├── docs/
│   ├── architecture.md            # Complete architecture and functional specification
│   ├── build-log.md               # Step-by-step design decisions and QA results
│   ├── ipc-protocol.md            # IPC binary protocol reference
│   ├── governance-mapping.md      # Framework control mapping
│   ├── cowork-bom-arduino.md      # Peripherals BOM brief (cameras, cables, power)
│   └── cowork-session-summary.md  # This document
├── alvik-firmware/
│   ├── ipc_codec.py               # MicroPython IPC codec (CPython-testable)
│   ├── motor_map.py               # IPC ActionType to motor API mapping
│   ├── main.py                    # Alvik governance firmware (4 gates)
│   └── pyproject.toml
├── audit-service/
│   ├── logger.py                  # AuditLogger, AuditEvent, session management
│   ├── dashboard/                 # Flask read-only audit dashboard
│   │   ├── app.py
│   │   └── models.py
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── tests/                     # 72 tests, 96.13% coverage
└── linux-stack/
    ├── ipc/
    │   ├── codec.py                # Binary IPC protocol, 8 message types, CRC-16/CCITT
    │   └── mock_peer.py            # Pty-based STM32H5 simulator
    ├── perception/
    │   ├── base.py                 # DetectionResult dataclass, PerceptionPipeline ABC
    │   ├── backends.py             # Stub backends + NullPipeline
    │   ├── backends_impl.py        # YOLO-X, MediaPipe, PoseNet production backends
    │   ├── capture.py              # V4L2, synthetic, file frame sources
    │   ├── network.py              # Length-prefixed JSON TCP transport (port 9100)
    │   └── uno_q_service.py        # Multi-backend pipeline, stub fallback, camera loop
    ├── governance/
    │   ├── filter.py               # GovernanceFilter, DEFAULT_COMMAND_MAP
    │   └── ventuno_q_service.py    # TCP receive, filter, IPC dispatch to Alvik
    ├── requirements.txt
    ├── pyproject.toml
    └── tests/                      # 241 tests, 95.76% coverage
```

---

## Content Deliverables Produced

### LinkedIn Pulse Article

**Title:** "When AI Controls Physical Systems: Governance Must Be a Hardware Invariant, Not a Policy Document"
**Pillar:** B: AI Governance, Regulation and the Infrastructure It Forces
**Format:** C-suite editorial (7 mandatory sections), ~1,100 words
**Publish date:** Thursday 7 August 2026 (editorial calendar: Thursdays 10:00 CET)

Pull quote:
> *"ISO 42001 requires organisations to monitor and measure AI performance. It does not specify where in the system stack to place the monitor. For physical AI, that question is not optional: the wrong answer can injure someone."*

Sources (all open-access): IFR World Robotics 2025, OSHA severe injury data, EU AI Act (Regulation 2024/1689), ISO 42001:2023, NIST AI RMF 1.0, CEDAR-42001 (arxiv:2606.21276), Arduino VENTUNO Q, Digital Omnibus Package.

What This Article Does Not Resolve (four genuine open tensions):
- Threshold calibration is an engineering judgment, not a governance standard
- The audit log is tamper-evident, not tamper-proof
- The STM32H5 firmware sits outside this governance framework
- ISO 42001 certification does not validate this architecture

Hashtags: `#PhysicalAI #AIGovernance #EUAIAct #EdgeAI #RoboticsSafety #ISO42001`

---

### LinkedIn Companion Feed Post

~1,680 chars. Hook: *"Most AI governance frameworks were built for software. Not for systems that move."*

Four bullet takeaways. Link in first comment (added after article publishes). First-comment template pre-written with three key source citations and repo URL.

---

### LinkedIn Arduino Outreach Post

Tags Fabio Violante (VP & GM, Arduino). Technically specific: cites the binary protocol, the dual-gate float32 edge case, and the 313-test count. Invites hardware contribution programme / developer preview participation.

Note: do not tag Massimo Banzi (no longer with Arduino).

---

### Technical Tutorial

**Title:** "Building Governance-First Physical AI: A Step-by-Step Architecture on the Arduino VENTUNO Q"
**Format:** Markdown suitable for glossolalie.pro, dev.to, or Substack

Structured around the eight-step build sequence. Each section includes code snippets, design rationale, and test patterns. Closes with "What This Architecture Does Not Solve Yet" section.

---

## Key Architectural Decisions

**Why log-before-act is structural, not procedural:** `audit_ref` is a return value from `log_event()`. The send is inside an `if should_send:` block that follows the log call. There is no code path that sends without logging; the structure prevents it.

**Why the mock peer uses a pty, not a socket:** The real UART is opened as a file descriptor with `buffering=0`. The test fixtures open `peer.device` the same way. The test-to-production interface is identical.

**Why `audit_ref=0` is rejected at the wire level:** `log_event()` returns a SQLite rowid, which is always >= 1. A value of 0 indicates the frame was constructed without logging. The MCU's rejection of `audit_ref=0` makes this invariant enforceable even if the Linux-side code is patched to skip logging.

**Why `_build_backends()` catches `OSError`:** If a production backend (e.g. MediaPipe) is installed but its model weight file (`.task`) is absent, Python raises `FileNotFoundError` (a subclass of `OSError`). Catching `OSError` alongside `ImportError` and `RuntimeError` ensures the service always starts with stubs rather than crashing, matching CI behaviour.

**Why the command map defaults to HALT for unknown labels:** Unknown inputs in a safety-critical system are conservatively treated as hazardous. `_DEFAULT_ACTION = (ActionType.HALT, 0)` ensures new labels from an updated model do not produce unexpected actuation until the map is explicitly updated.

**Why `stm32_ack` is nullable (NULL, not False) on timeout:** NULL means "no response received." False means "response received: rejected." The distinction is forensically meaningful. A NULL entry may indicate a network/serial fault; a False entry indicates the MCU made a deliberate decision.

**Why Alvik firmware has four independent gates:** The governance argument requires that safety invariants survive to the physical actuator, not just to the governance brain. The Alvik STM32F411 gate is the last line of defence: it runs on a separate processor, in a separate language (MicroPython), with no shared code path with the Linux governance filter.

**Why the network transport is length-prefixed JSON, not protobuf or gRPC:** Simplicity for a demonstrator without hardware access. The `DetectionResult` dataclass is JSON-serialisable with no schema compilation step. The 4-byte length prefix gives framing without a TLS or HTTP dependency. Upgrade path to gRPC is straightforward: the `DetectionResultServer/Client` interface is the only change surface.

---

## Open Items

| Item | Notes |
|---|---|
| Camera module for UNO Q 4GB | No Arduino-native CSI module confirmed; sourcing via Arducam MIPI or Waveshare pending store research (see `docs/cowork-bom-arduino.md`) |
| USB-C cables, power supplies | Peripherals BOM pending Cowork agent run against store.arduino.cc |
| Real hardware validation | All governance logic tested; requires physical boards + camera + cables to validate on-device |
| Confidence threshold calibration | 0.70 is an engineering judgment; no published standard maps confidence to injury probability for HRC |
| Audit log tamper-proofing | SQLite WAL is tamper-evident only; production needs signed entries or hardware-secured storage |
| Dashboard authentication | None (local-only demonstrator); production needs mTLS or token auth |
| GitHub repo description | Currently stale; update to: "AI governance controls enforced in hardware across three Arduino boards: UNO Q 4GB (perception), VENTUNO Q (audit log + IPC), Alvik (motor execution). Log-before-act invariant, dual confidence gate, hardware kill switch. 313 tests, 95%+ coverage, CI without physical hardware." |
| glossolalie.pro page | `governed-edge-ai.html` not yet created; brief in `docs/cowork-website-governed-edge-ai.md` |
