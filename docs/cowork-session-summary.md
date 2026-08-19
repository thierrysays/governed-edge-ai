# Governed Edge AI: Project Summary (Eleven Steps Shipped)

**Project:** governed-edge-ai Physical AI Demonstrator
**Repository:** https://github.com/thierrysays/governed-edge-ai (public, main branch)
**Last updated:** 19 August 2026
**Status:** Eleven build steps shipped, six more designed and scheduled. Five boards in the architecture, four of them running code today. 703 tests, 100% line coverage on both modules, full QA suite green.

---

## What This Project Is

A working demonstrator of governance-first Physical AI. Three theses, added one per redesign:

1. For physical AI systems, safety invariants must be enforced at the hardware and protocol level, not only in policy documents.
2. The function that supervises a system must not depend on that system. *(Step 9)*
3. A governance control must not be attached to the component it governs, and must not need that component's cooperation to work. *(Step 11)*

No actuation occurs without a prior audit log entry, enforced at the IPC protocol layer: the MCU rejects any `CommandRequest` frame with `audit_ref == 0`. No audit log entry goes unwitnessed by a board the governance host does not control, no message that host can send releases the oversight override, and no software anywhere can restore motor power once the arbiter has opened the relay contact.

**Stack:** Python 3.11, SQLite WAL-mode, binary IPC with CRC-16/CCITT, TCP JSON transport, MicroPython firmware, Arduino C++ firmware, SHA-256 attestation chain, pytest, ruff, mypy strict, bandit, pip-audit. 703 tests across two modules, hardware-free via two pty-based co-processor models, a simulated bistable relay and loopback TCP.

---

## Five-Board Architecture

```
                       Nesso N1  (TPX00227)
                       Out-of-band console. Designed, build step 13.
                       Verdicts out, signed HALT lift back
                             ▲                    │
                             │                    ▼
UNO Q 4GB  ───────────► VENTUNO Q ──────────────────────► Alvik
Witness         2.5GbE  Decision path, revocable    USB   Governed body
Independent model       GovernanceFilter                  Motors, ToF, IMU
Disagreement            SQLite + SHA-256 chain                  ▲
forces HALT                     ▲                               │ motor +V
                  heartbeat +   │  reports only                 │
                  chain digests │                               │
                           UNO R4 WiFi                          │
                           Safety arbiter, off the command chain│
                           Override button, 12x8 annunciator    │
                           64 retained audit digests            │
                                │ Qwiic I2C 0x2A                │
                                └──► Latch Relay ───────────────┘
                                     Bistable contact
                                     + antivalent sense pair
```

**Four boards are running code. The Nesso N1 is designed and scheduled** (step 13). Everything in this document that is marked shipped runs on the other four plus the relay.

| Board | Single job | Decides? | Enforces? | Key specs |
|---|---|---|---|---|
| Arduino UNO Q 4GB | Witness: an independent second observation | no | no | Qualcomm QRB2210, dual ISP 13 MP / 30 fps, STM32U585, 4 GB LPDDR4 |
| Arduino VENTUNO Q | Decision: perception, governance filter, audit journal | yes | no | Qualcomm IQ-8275 NPU 40 TOPS, STM32H5, 16 GB RAM |
| Arduino Alvik | Governed body: executes, and may refuse | no | self only | ESP32-S3 + STM32F411, wheeled robot, ToF 8x8, 6-axis IMU |
| Arduino UNO R4 WiFi | Safety arbiter: relay, buttons, annunciator, digest witness | no | **yes** | Renesas RA4M1 + ESP32-S3, 12x8 LED matrix, 32 KB SRAM, Qwiic |
| Arduino Nesso N1 | Out-of-band human supervision | no | via signed lift | ESP32-C6, 1.14" touchscreen, Wi-Fi 6 / BLE / LoRa, battery |

**No board both decides and enforces.** That sentence is the whole architecture, and it is checkable by looking at the wiring rather than by reading a policy.

On the arbiter's own Qwiic bus, deliberately not the decision host's: a **Modulino Latch Relay** (ABX00138) whose bistable contact sits in series with the Alvik's motor supply, plus Distance and Movement modules for evidence outside the vision pipeline (step 15).

**Cameras:** Arducam IMX219 8 MP, two of them, splayed for roughly 120° of coverage. Both CSI ribbon adapters ship with the modules.

**Data flow:** the UNO Q witnesses, the VENTUNO Q perceives and decides. `GovernanceFilter` asks the arbiter whether it may act, logs every detection to SQLite, folds the stored row into a SHA-256 hash chain and publishes the head to the arbiter, then transmits an audited `CommandRequest`. The Alvik validates `audit_ref` and executes, if the relay contact is closed.

**Two orderings carry the argument.** The digest reaches the witness before the command reaches the robot, which turns the retained digests into evidence rather than a log of a log. And the contact opens before the override is announced, so if the announcement is what fails, the motors are already isolated.

**Why the arbiter is the least capable board here, deliberately:** the whole firmware is a few hundred lines of C++ with no scheduler, no filesystem and no network stack in the default build. Small enough to read in one sitting, which is what a supervisor should be. The argument is that the supervisor should be simpler than the supervised, so its correctness is checkable by inspection.

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
| Human override authority | ISO 42001, COBIT APO | Physical NC button on the UNO R4 WiFi, outside the AI stack. Latches; no protocol message clears it. |
| Alvik firmware gate | Defence-in-depth (Tier 3) | Four independent governance gates before any motor command executes |
| Independent oversight function | ISO 42001 Clause 9.2, IIA Three Lines | Tier 0 is not on the command path, uses its own link, enforces through a relay contact no message can close |
| Enforcement that outlives its enforcer | ISO 42001 Clause 8.4 | Bistable contact in the motor supply. Holds with no coil current through a power cut at every board and a reboot of the decision host. |
| Enforcement the governed cannot defeat | COBIT APO01 | The contact is in the supply, so the Alvik has no pin to stop honouring. Reflashing it changes nothing. |
| Control read back, not assumed | ISO 42001 Clause 9.1 | An antivalent opto pair observes the contact every 100 ms. Any non-complementary reading is UNKNOWN, and nothing rounds UNKNOWN up to isolation. |
| Witness-before-act | ISO 42001 Clause 9.1 | The audit chain head reaches the oversight node before the command frame is written |
| Off-host tamper evidence | ISO 42001 Clause 9.1 | 64 SHA-256 chain digests retained on a board the governance host does not control |
| Governance liveness | COBIT DSS02 | The oversight node latches an override if the governance tier stops reporting for 2 s. A software restart does not clear it. |
| Fail-closed on oversight loss | ISO 42001 Clause 8.4 | Silence from the oversight node counts as a veto on the governance side |

---

## Build Sequence (Eleven Steps)

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

Binary protocol between the boards. Eight message types at this step; five more were added on the oversight link in step 9.

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

### Step 9: Oversight Tier (`linux-stack/oversight/`, `r4-supervisor/`)

A separate oversight board and everything that followed: an attestation hash chain, a supervisor link, a pty-based reference model, an Arduino C++ firmware, five new IPC message types, two new governance invariants and a third audit actor.

**The weakness it corrects.** In the three-board version the human override lived inside the system it was meant to override. The gesture HALT travelled through the AI perception pipeline; the kill switch sat on the actuation MCU the governance node commands. Both were real controls, and both went down with the thing they supervised. In Three Lines Model terms, the architecture had a strong second line and no third.

**Two enforcement paths** (the physical one was a GPIO line at this step; step 11 replaced it):

| Path | Mechanism | Survives |
|---|---|---|
| Soft veto | `OVERRIDE_ASSERT` over serial; the filter stops transmitting | A hung or misbehaving perception tier |
| Physical | A GPIO line into the Alvik kill-switch input, superseded at step 11 by a relay contact in the motor supply | A compromised governance host, a cut cable, a forged `OVERRIDE_CLEAR` |

**Attestation.** Each stored audit row is read back from SQLite and folded into `digest_n = SHA-256(digest_{n-1} || canonical(row_n))`. The head is published to the R4, which retains the last 64 off-host. Reconciling a recomputed chain against those digests detects edits, deletions, backdating and reordering. The chain is unkeyed: it protects rows already witnessed, not rows written after a host compromise. That limit is documented rather than glossed.

**Firmware parity.** `MockR4Supervisor` is the executable specification; the C++ is the port. `test_r4_firmware_parity.py` compiles the firmware logic for the host with `-Wall -Wextra -Werror` and checks byte-identical frames, identical verdict sequences, identical state transitions and identical constants.

**Two defects found by the adversarial tests, not by review:** a missing frame-length guard that let one hostile header wedge a link permanently, and a failed transmit that left the audit log claiming a command had been sent. Both fixed, both with regression tests.

---

### Step 10: Architecture Reconciliation (`docs/architecture-reconciliation.md`)

The published governance-chain diagram read against the codebase: a fifteen-row delta register, eight decisions taken, four reasoned defaults, and a rationale for the five-board configuration.

**The configuration.** Five boards, one job each. The Modulino Hub, Buttons, Pixels and Buzzer were dropped as redundant with the arbiter, which already has buttons, a matrix and a Qwiic port. Distance and Movement stay: they are the two doing real work, a safety envelope outside the vision pipeline and proof of stop.

**The decision that unblocked the most.** Governance modules attach to the arbiter's Qwiic bus, not the decision host's. The rule that produced the third line says a governance module must not hang off the thing it governs; the same rule says it must not hang off the thing that decides. The arbiter neither decides nor is governed, which makes it the only board that qualifies. A side effect is that the arbiter role stays on the R4 permanently, which takes an unpublished pinout off the critical path for steps 11 to 15.

**The camera closed.** Arducam IMX219 8 MP, two of them, splayed for roughly 120°. Three of its specifications have governance consequences rather than image-quality ones, and all three went into the threat model: 200 mm minimum focus leaves the near field blurred exactly where the risk is highest, rolling shutter means a frame is not a moment, and a 62.2° cone means the audit log will faithfully record that nothing was detected in a blind sector.

---

### Step 11: The Latch Relay (`linux-stack/oversight/latch.py`, `r4-supervisor/latch.h/.cpp`)

The physical enforcement path, rebuilt. Two new IPC message types, a new reject reason, a new override reason, a fifth annunciator glyph, and the retirement of the GPIO line into the Alvik.

**Why the line had to go.** Two faults, and the second is worse.

It **failed open**. Cut power to the arbiter and the line released, so a power cut at the oversight node un-isolated the motors. What made this uncomfortable is that the suite had 611 tests and none could have caught it: the mocks modelled a state machine, so there was no power to lose. Coverage does not find a fault whose failure mode the model has no vocabulary for.

It **needed the governed component's cooperation**. The line worked only because the Alvik's firmware chose to read that pin. Firmware on the board under review is a software gate wearing a hardware costume. This broke the project's own design rule while the documentation described it as the path that could not be reached from software.

**Two sources of truth.** The module's I2C register is a cross-check, not an observation: a small MCU behind an I2C interface most likely echoes the last command it accepted rather than observing the contact, and believing it would reproduce the exact error the read-back exists to remove.

**The observation is antivalent, and that came out of writing the deployment guide.** The first implementation read one pin: high meant open. Asking what that pin reads when its wire is cut gave the answer "open", which the arbiter would have reported as *the motors are isolated*. Wiring it the other way only moves the problem: whichever way a single input is arranged, one of its two readings is also what a broken wire produces. Two opto-isolated channels that must disagree with each other fixes it, and any non-complementary pair decodes to UNKNOWN, which nothing rounds up to isolation.

**Who owns the relay.** The arbiter, exclusively. The governance tier may request OPEN, always honoured because more ways to stop are safe. It may request CLOSED, refused outright while an override stands. That asymmetry is why the relay is not on the decision host's bus.

---

## What Is Designed But Not Built

Six steps, in dependency order. Steps 12 to 16 are testable hardware-free on the existing pattern; step 17 is not.

| Step | Work | Depends on |
|---|---|---|
| 12 | Arbiter as governance bus owner: I2C layer, third button, ALLOW / GATED / HALT glyphs | 11 |
| 13 | Nesso N1: verdict stream, display, signed HALT lift, key pairing | 11, 12 |
| 14 | Audit journal signing, countersigned by the Nesso | 13 |
| 15 | Distance and Movement: evidence outside the vision pipeline, proof of stop | 12 |
| 16 | Witness UNO Q and the agreement gate | none, parallel |
| 17 | STM32H5 Zephyr firmware, motor-side timing only | Unpublished pinout |

---

## QA Baseline

| Module | Tests | Coverage |
|---|---|---|
| linux-stack | 604 | 100% |
| audit-service | 99 | 100% |
| **Total** | **703** | **100% on both, gate at 98%** |

All checks pass: ruff, mypy strict, bandit (no issues), pip-audit (no CVEs).

```bash
make qa        # lint + typecheck + security + full test suite
make smoke     # fast sanity pass, hardware-free
```

All tests run without physical hardware. CI reproduces the full stack: synthetic camera frames, a pty-based mock STM32H5, a pty-based mock oversight node, loopback TCP transport, and a host build of the R4 firmware logic.

Two suites are worth naming. `test_security_oversight.py` attacks the design from four threat positions and asserts what does *not* hold as well as what does. `test_r4_firmware_parity.py` holds the C++ to the Python model.

**Not tested:** the Arduino hardware layer. Pin timing, the LED matrix driver, Wi-Fi, serial throughput at 921600 baud, and the electrical behaviour of the relay and its two sense channels all need the physical rig. Every timing figure in the protocol specification is a design target, not a measurement.

**Three real defects were found by this suite rather than by review.** A missing frame-length guard that let one hostile header wedge a link permanently. A failed transmit that left the audit log claiming a command had been sent. And the GPIO kill line failing open on power loss, which no test could have caught because the mocks modelled logic and had no power to lose. A fourth came out of writing the deployment guide rather than the tests: a single-channel sense line reading a cut wire as "motors isolated".

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
│   ├── deployment-guide.md        # Step-by-step build from bare metal, for a first-time reader
│   ├── architecture-reconciliation.md  # Published design vs codebase: deltas and rationale
│   ├── cowork-bom-arduino.md      # Peripherals BOM brief (relay, modules, cameras, cables, power)
│   ├── cowork-website-governed-edge-ai.md  # Brief for the glossolalie.pro project page
│   └── cowork-session-summary.md  # This document
├── alvik-firmware/
│   ├── ipc_codec.py               # MicroPython IPC codec (CPython-testable)
│   ├── motor_map.py               # IPC ActionType to motor API mapping
│   ├── main.py                    # Alvik governance firmware (4 gates)
│   └── pyproject.toml
├── r4-supervisor/                 # UNO R4 WiFi oversight firmware (Arduino C++)
│   ├── r4_supervisor.ino          # Pins, LED matrix, serial, optional Wi-Fi console
│   ├── supervisor_state.h/.cpp    # State machine, host-compilable (no Arduino headers)
│   ├── latch.h/.cpp               # Latch relay driver, hardware injected as function pointers
│   ├── ipc_frame.h/.cpp           # IPC codec, oversight subset only
│   ├── test/parity_harness.cpp    # Host driver for the parity test suite
│   └── README.md
├── audit-service/
│   ├── logger.py                  # AuditLogger, AuditEvent, session management
│   ├── dashboard/                 # Flask read-only audit dashboard
│   │   ├── app.py
│   │   └── models.py
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── tests/                     # 99 tests, 100% coverage
└── linux-stack/
    ├── ipc/
    │   ├── codec.py                # Binary IPC protocol, 15 message types, CRC-16/CCITT
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
    │   └── ventuno_q_service.py    # TCP receive, filter, IPC dispatch to Alvik and R4
    ├── oversight/
    │   ├── attestation.py          # SHA-256 audit chain, offline tamper reconciliation
    │   ├── latch.py                # Bistable relay driver, antivalent read-back, simulator
    │   ├── supervisor_link.py      # VENTUNO Q side of the oversight link, fail-closed
    │   └── mock_supervisor.py      # Pty-based R4 model, the firmware's specification
    ├── requirements.txt
    ├── pyproject.toml
    └── tests/                      # 604 tests, 100% coverage
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

### LinkedIn Commentary Post (to add as a comment on the published article)

Written to be posted as a **comment on the existing article**, not as a new feed post. It reports what changed at v3.0.0, and its value is that it reports a fault found in the author's own design rather than a milestone.

**Length: 1,168 characters.** LinkedIn caps comments at **1,250**, not the 3,000 that applies to posts, so this is written to the tighter ceiling and has roughly 80 characters of headroom.

---

An update, because it came from finding a fault in my own design.

The physical stop used to be a signal wire from the oversight board into a pin on the robot. It released when that board lost power. And it worked only because the robot's firmware chose to read the pin, which makes the component under review a participant in its own restraint.

Neither fault showed up in 611 passing tests. The doubles modelled a state machine, so there was no power to lose. Coverage does not find a fault whose failure mode your model has no vocabulary for.

It is now a bistable relay contact in the motor supply. It holds with no current at all, and the robot has nothing left to agree to.

Writing the deployment instructions found the second one. The board read that contact back on a single sense line, and I had to answer what it reads when the wire is cut. The answer was "open", which the board reports as "the motors are isolated". On no evidence.

Two channels now, wired to disagree. Any fault reads as "cannot see", and nothing rounds that up to "isolated".

Governance controls fail in the direction you did not model.

v3.0.0: github.com/thierrysays/governed-edge-ai

---

**Notes for posting.**

No hashtags. It is a comment on an existing thread, not a discovery surface.

No test count for the current build. The argument is that a passing suite missed the fault, and quoting 703 tests in the same breath undercuts it. The 611 figure stays because it is the number that failed to catch anything.

The version reference is the last line rather than the first. The subject is the fault, not the release.

If the comment draws a reply asking what the two channels are, the follow-up fits in one comment: one opto is lit only while the contact is open, the other only while the motor rail is live, and exactly one should ever be lit. Both dark or both lit means the observation failed, not that the contact moved.

---

### Technical Tutorial

**Title:** "Building Governance-First Physical AI: A Step-by-Step Architecture on the Arduino VENTUNO Q"
**Format:** Markdown suitable for glossolalie.pro, dev.to, or Substack

Structured around the build sequence as it stood at step 8. Step 9 (the oversight tier) postdates the article and is the natural subject of a follow-up. Each section includes code snippets, design rationale, and test patterns. Closes with "What This Architecture Does Not Solve Yet" section.

---

## Key Architectural Decisions

**Why log-before-act is structural, not procedural:** `audit_ref` is a return value from `log_event()`. The send is inside an `if should_send:` block that follows the log call. There is no code path that sends without logging; the structure prevents it.

**Why the mock peer uses a pty, not a socket:** The real UART is opened as a file descriptor with `buffering=0`. The test fixtures open `peer.device` the same way. The test-to-production interface is identical.

**Why `audit_ref=0` is rejected at the wire level:** `log_event()` returns a SQLite rowid, which is always >= 1. A value of 0 indicates the frame was constructed without logging. The MCU's rejection of `audit_ref=0` makes this invariant enforceable even if the Linux-side code is patched to skip logging.

**Why `_build_backends()` catches `OSError`:** If a production backend (e.g. MediaPipe) is installed but its model weight file (`.task`) is absent, Python raises `FileNotFoundError` (a subclass of `OSError`). Catching `OSError` alongside `ImportError` and `RuntimeError` ensures the service always starts with stubs rather than crashing, matching CI behaviour.

**Why the command map defaults to HALT for unknown labels:** Unknown inputs in a safety-critical system are conservatively treated as hazardous. `_DEFAULT_ACTION = (ActionType.HALT, 0)` ensures new labels from an updated model do not produce unexpected actuation until the map is explicitly updated.

**Why `stm32_ack` is nullable (NULL, not False) on timeout:** NULL means "no response received." False means "response received: rejected." The distinction is forensically meaningful. A NULL entry may indicate a network/serial fault; a False entry indicates the MCU made a deliberate decision.

**Why Alvik firmware has four independent gates:** The governance argument requires that safety invariants survive to the physical actuator, not just to the governance brain. The Alvik STM32F411 gate is the last line of defence: it runs on a separate processor, in a separate language (MicroPython), with no shared code path with the Linux governance filter.

**Why the oversight node gets two enforcement paths, not one:** the soft veto travels on a serial link whose trust boundary is the physical cable. Anyone who can write to it can forge an `OVERRIDE_CLEAR`, and the test suite demonstrates exactly that. The relay contact in the motor supply is not reachable from any link: the governance tier may ask for it to be opened, which is always honoured, and asking for it to be closed is refused while an override stands. Building only the soft path would have produced a single point of failure that the diagram would then have described as independent.

**Why the override latches and no message clears it:** the protocol contains no `OVERRIDE_DENY` and no clear the governance tier can send. Releasing an override is a physical act at the oversight board. The test that separates a second-line control from third-line assurance is whether the supervised function can switch it off, and `test_no_message_type_clears_an_override` asks that question in code.

**Why the attestation chain hashes the stored row, not the intended one:** `fetch_event()` reads the row back from SQLite before hashing. Hashing the caller's `AuditEvent` would have been free and would have proved nothing about what the database actually holds. The cost is one indexed SELECT per event.

**Why `stm32_ack`, `flag` and `notes` are excluded from the chain:** all three are written after the row is created, so committing to them would break the chain on every legitimate update. The chain covers what the row asserted at the moment the command decision was taken.

**Why the contact is held open from boot until the first heartbeat:** not a latch and no arming step, just `override || !heartbeat_seen`. A governance tier that has not yet said anything has not yet earned the authority to move a robot. Latching at boot was correct but forced a manual arming step before every run; releasing at boot was convenient and wrong. Because the contact is bistable it comes up wherever it was left rather than in a safe default, so the arbiter opens it before anything else runs and starts from UNKNOWN rather than assuming.

**Why physical enforcement moved off the governed board:** the GPIO kill line worked only because the Alvik's firmware chose to read that pin. A governance control that needs the cooperation of the component under review is a software gate wearing a hardware costume, and reflashing the Alvik would have removed it. A contact in the motor supply needs no cooperation. The same rule that produced the third line in the first place produced this, applied one level further out.

**Why the contact read-back uses two sense channels:** a single sense input cannot distinguish a contact position from a cut wire, and one of the two positions it would confuse with a fault is "open", which reads as "the motors are isolated". Two opto-isolated channels wired antivalent means every fault in the observation decodes to UNKNOWN, which nothing rounds up to isolation. The cost is availability: a broken sense wire stops the rig. That is the correct direction for this trade.

**Why the relay's own I2C register is not trusted as the observation:** a small MCU behind an I2C interface most likely echoes the last command it accepted rather than observing the contact. Believing it would reproduce exactly the error the read-back exists to remove, which is the same shape as trusting `stm32_ack` on its own.

**Why the network transport is length-prefixed JSON, not protobuf or gRPC:** Simplicity for a demonstrator without hardware access. The `DetectionResult` dataclass is JSON-serialisable with no schema compilation step. The 4-byte length prefix gives framing without a TLS or HTTP dependency. Upgrade path to gRPC is straightforward: the `DetectionResultServer/Client` interface is the only change surface.

---

## Open Items

| Item | Notes |
|---|---|
| Real hardware validation | All governance logic is tested. The rig itself needs the boards, the relay, the sense circuit and the cameras on a bench. |
| Latch module datasheet check | Three assumptions are cheap to settle and each changes only its own paragraph if wrong: that the module's register echoes its command, that the I2C address is `0x2A`, and that a 50 ms coil pulse is right. |
| Sense channel B current draw | A continuous milliamp-scale load on the motor supply while the contact is closed. Measure, then size the series resistor. |
| CSI capture on the VENTUNO Q | The IMX219 has a mainline driver, but the Qualcomm camera pipeline needs a sensor driver bound through CAMSS and a device-tree entry. A USB UVC webcam on the same capture abstraction is the fallback and is already in the design for the witness node. |
| Camera splay angle | Decided at roughly 52° between axes. It becomes a calibrated, documented parameter, because the safety envelope is defined by it and a mount that shifts is a control that drifts silently. |
| Attestation digest signing | The chain is unkeyed: it protects rows already witnessed, but a host controlling both the database and the oversight link can forge a consistent chain going forward. Signing with a key held only off-host is step 14. |
| Blocking on `ATTEST_ACK` | The filter does not wait for the oversight node's verdict before transmitting. An attestation fault stops the next command, not the one in flight. Still open. |
| Retained digest read-back | Currently the Wi-Fi console, which is the least satisfying part of the design. The Nesso N1 is the better answer, at step 13. |
| Movement module mounting | Proof of stop needs the IMU on the robot and the reader off it. Wireless telemetry was chosen; the reader still cannot be the Alvik without collapsing the control into self-reporting by the board being stopped. |
| Confidence threshold calibration | 0.70 is an engineering judgment. No published standard maps a confidence score to injury probability for human-robot collaboration. |
| Dashboard authentication | None. Local-only demonstrator; production needs mTLS or token auth. |
| Tags `v1.0.0` and `v2.0.0` | Created locally with the author as tagger. Pushing tag refs is blocked from the build session and needs `git fetch origin && git push origin v1.0.0 v2.0.0` from the author's own machine. |
| `v2.0.0` release notes | Written before step 11 and now partly superseded: they present the R4 as a tier wired to the Alvik. Rewrite before publishing the release. |

---

## Repository Description and Topics

Current values are stale. Replace with:

**Description** (324 characters, GitHub allows 350):

> Governance controls enforced in circuitry, not policy, across five Arduino boards. No actuation without a prior audit entry, no audit entry unwitnessed by a board the host does not control, and a bistable relay in the motor supply that no software can close. 703 tests, 100% coverage, full stack runs in CI with no hardware.

**Topics:**

`ai-governance` · `physical-ai` · `edge-ai` · `arduino` · `functional-safety` · `iso-42001` · `eu-ai-act` · `nist-ai-rmf` · `audit-trail` · `tamper-evident` · `embedded-systems` · `robotics-safety` · `human-oversight` · `micropython` · `real-time-systems`
