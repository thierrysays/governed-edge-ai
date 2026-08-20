# governed-edge-ai

Reference architecture demonstrating that AI governance principles can be enforced in circuitry, not merely documented as policy.

The stack runs across five Arduino boards, one job each. A UNO Q 4GB is the independent witness. A VENTUNO Q holds perception, the governance filter and the audit journal. An Alvik mobile robot is the governed body. A UNO R4 WiFi is the safety arbiter, outside the command chain, holding a bistable relay in the motor supply. A Nesso N1 is the out-of-band operator console.

No actuation occurs without a prior audit log entry. No audit log entry goes unwitnessed by a board the governance host does not control. And no software anywhere can restore motor power once the arbiter has opened the contact. The first two are enforced at the protocol layer, the third at the end of a wire.

This project began with the Arduino VENTUNO Q announcement. It is the first embedded project from Glossolalie Advisory, and the premise is explicit: the governance frameworks argued for in boardrooms should survive contact with hardware.

## Why this matters

Governance frameworks tend to live in documents. They prescribe human oversight, segregation of duties, and auditability as policy objectives. This project treats those same objectives as engineering constraints: the NPU never has a direct path to actuators, every inference-to-actuation pair is written to an append-only audit log before the command is transmitted, the STM32 rejects any command that does not carry a valid audit reference, and a board outside the whole chain holds the evidence and the stop button.

The result is a reference architecture that a governance professional can point at and say: this is what a control objective looks like when implemented, not merely stated. It also shows what the implementation costs, and where it stops working, which the documents rarely do.

## Non-negotiable design principle: authority separation

The AI perception stack (Linux side) may recommend or request an action. It must never have the sole path to execute one. Every actuation command passes through the STM32 real-time core, which enforces its own independent confidence gate and rejects commands without a confirmed audit reference.

A second principle was added with the oversight node, and it corrects a real weakness in the three-board version: **the function that supervises a system must not depend on that system.** The human override used to live inside the stack it was meant to override. The gesture HALT travelled through the AI perception pipeline; the kill switch sat on the actuation MCU that the governance node itself commands. Both were real controls, and both went down with the thing they supervised.

The UNO R4 WiFi is not on the command path, is reached by its own link, and holds a bistable relay contact in the Alvik's motor supply that no message on any link can reach. Nothing the governance tier can send releases it: the protocol contains no such message, and the test suite proves it by throwing the entire outbound vocabulary at a latched node. The governance tier may ask for the contact to be opened, which is always honoured, and it may ask for it to be closed, which is refused outright while an override stands.

Neither principle is a prototyping convenience to be refactored later. They are the governance argument.

## Hardware

Five boards, all owned. No external actuator hardware required beyond the Alvik's built-in motors.

| Board | Role | Key specs |
|---|---|---|
| Arduino UNO Q 4GB | Perception node | Qualcomm QRB2210, dual ISP 13 MP / 30 fps, STM32U585, 4 GB LPDDR4, Wi-Fi 5 |
| Arduino VENTUNO Q | Governance brain | Qualcomm IQ-8275 NPU 40 TOPS, STM32H5, 16 GB RAM |
| Arduino Alvik | Physical body | ESP32-S3 + STM32F411, wheeled robot, ToF 8×8, 6-axis IMU, line follower |
| Arduino UNO R4 WiFi | Safety arbiter | Renesas RA4M1 + ESP32-S3, 12x8 LED matrix, 32 KB SRAM, Qwiic |
| Arduino Nesso N1 | Out-of-band console | ESP32-C6, 1.14" touchscreen, Wi-Fi 6 / BLE / LoRa, battery |

On the arbiter's own Qwiic bus, not the decision host's: a **Modulino Latch Relay** (ABX00138) whose bistable contact sits in series with the motor supply, plus Distance and Movement modules for evidence outside the vision pipeline.

The R4 is the least capable board here, and that is the point. The whole arbiter firmware is a few hundred lines of C++ with no scheduler, no filesystem and no network stack in the default build: small enough to read in one sitting, which is what a supervisor should be.

**Cameras sourced:** Arducam IMX219 8 MP, two of them, splayed for roughly 120° of coverage.

## Architecture

```
                       Nesso N1
                       out-of-band console, battery, touchscreen
                       verdicts out · signed HALT lift back
                             ▲                    │
                             │                    ▼
UNO Q 4GB  ───────────► VENTUNO Q ──────────────────────► Alvik
witness         2.5GbE  decision path, revocable    USB   governed body
UVC webcam              perception · GovernanceFilter     motors · ToF · IMU
independent model       signed audit journal                    ▲
disagreement                    ▲                               │ motor +V
forces HALT       heartbeat +   │  reports only                 │
                  chain digests │                               │
                           UNO R4 WiFi                          │
                           safety arbiter                       │
                           E-STOP / ARM / ACK · annunciator     │
                           64 retained audit digests            │
                                │ Qwiic I2C                     │
                                └──► Latch Relay ───────────────┘
                                     bistable · 0x2A
                                     + GPIO sense line
```

Data flow: the UNO Q witnesses, the VENTUNO Q perceives and decides. The `GovernanceFilter` asks the arbiter whether it may act, logs every detection to SQLite, folds the stored row into a SHA-256 hash chain and publishes the head to the arbiter, then transmits an audited `CommandRequest`. The Alvik validates `audit_ref` and executes.

Two orderings carry the argument. **The digest reaches the witness before the command reaches the robot**, which turns the retained digests into evidence rather than a log of a log. And **the contact opens before the override is announced**, so if the announcement is what fails, the motors are already isolated.

## Governance controls implemented

| Control objective | Framework reference | Implementation |
|---|---|---|
| Log-before-act | ISO 42001 Clause 9.1 | `audit_ref` (SQLite rowid >= 1) obtained before any command frame is transmitted |
| No actuation without audit | ISO 42001, NIST AI RMF GOVERN | STM32H5 rejects `CommandRequest` with `audit_ref = 0` at the protocol layer |
| Confidence gate (dual-layer) | Defence-in-depth | Linux gate at 0.70 float64; STM32H5 gate at 0.70 float32, independent enforcement |
| One command per frame | Segregation of duties | Highest-confidence detection only; all others logged as suppressed |
| Full suppression record | Auditability | Every detection logged regardless of whether a command was sent |
| ACK/REJECT tracking | ISO 42001 monitoring | `stm32_ack` column updated after MCU response; NULL on timeout (forensically meaningful) |
| Human override authority | ISO 42001, COBIT APO | Physical NC button on the arbiter, outside the AI stack entirely. Latches; no protocol message clears it. |
| Enforcement that outlives its enforcer | ISO 42001 Clause 8.4 | Bistable contact in the motor supply. Holds with no coil current through a power cut at every board and a reboot of the decision host. |
| Enforcement the governed cannot defeat | COBIT APO01 | The contact is in the supply, so the Alvik has no pin to stop honouring. Reflashing it changes nothing. |
| Control read back, not assumed | ISO 42001 Clause 9.1 | A GPIO sense line observes the contact. Disagreement with the module's own register latches an override on both sides independently. |
| Independent oversight function | ISO 42001 Clause 9.2, COBIT MEA02 | Tier 0 is not on the command path, uses its own link, and enforces through a relay contact no message can close |
| Witness-before-act | ISO 42001 Clause 9.1 | SHA-256 audit chain head published to the oversight node before the command frame is written |
| Off-host tamper evidence | ISO 42001 Clause 9.1, NIST AI RMF MEASURE | 64 chain digests retained on a board the governance host does not control. Rewriting the SQLite log is detected on reconciliation. |
| Governance liveness | COBIT DSS02 | The oversight node latches an override if the governance tier stops reporting for 2 s. Restarting the software does not clear it. |
| Fail-closed on oversight loss | ISO 42001 Clause 8.4 | Silence from the oversight node counts as a veto. A supervisor that cannot be reached is not a satisfied supervisor. |

## Software stack

All eleven build steps are complete.

| Module | Location | Description |
|---|---|---|
| IPC codec | `linux-stack/ipc/codec.py` | Binary protocol, 15 message types across two links, CRC-16/CCITT, `FrameParser` |
| Mock STM32H5 | `linux-stack/ipc/mock_peer.py` | Pty-based hardware simulator, full state machine, local test input |
| Perception interface | `linux-stack/perception/base.py` | `DetectionResult` dataclass, `PerceptionPipeline` ABC, stub backends |
| Camera capture | `linux-stack/perception/capture.py` | V4L2, synthetic, and file frame sources |
| Production backends | `linux-stack/perception/backends_impl.py` | YOLO-X, MediaPipe Hands, PoseNet (stub fallback in CI) |
| DetectionResult transport | `linux-stack/perception/network.py` | Length-prefixed JSON over TCP (UNO Q to VENTUNO Q) |
| UNO Q perception service | `linux-stack/perception/uno_q_service.py` | Multi-backend pipeline, stub fallback, camera loop |
| Governance filter | `linux-stack/governance/filter.py` | `GovernanceFilter`, `DEFAULT_COMMAND_MAP`, log-before-act enforcement |
| VENTUNO Q governance service | `linux-stack/governance/ventuno_q_service.py` | TCP receive, filter, IPC dispatch to Alvik |
| Audit logger | `audit-service/logger.py` | SQLite WAL-mode, append-only, `AuditEvent`, session management |
| Audit dashboard | `audit-service/dashboard/` | Flask read-only dashboard over the audit log |
| Alvik IPC codec | `alvik-firmware/ipc_codec.py` | MicroPython-compatible subset, CPython-testable |
| Alvik motor map | `alvik-firmware/motor_map.py` | Maps IPC action codes to `arduino_alvik` motor API |
| Alvik firmware | `alvik-firmware/main.py` | Four governance gates, USB-C serial I/O |
| Latch relay | `linux-stack/oversight/latch.py` | Bistable contact driver, two-source read-back, simulator that models power loss |
| Audit attestation | `linux-stack/oversight/attestation.py` | SHA-256 chain over audit rows, offline tamper reconciliation |
| Supervisor link | `linux-stack/oversight/supervisor_link.py` | VENTUNO Q side of the oversight link, fail-closed on loss |
| Mock oversight node | `linux-stack/oversight/mock_supervisor.py` | Pty-based R4 model. The executable specification for the firmware. |
| R4 arbiter firmware | `r4-supervisor/` | Arduino C++: state machine, latch driver, IPC subset, LED matrix |

## Inference models

- YOLO-X: object detection (person, robot_part, tool)
- MediaPipe Hands: gesture recognition (stop, thumbs_up, thumbs_down, swipe_left, swipe_right)
- MoveNet Lightning: proximity safety boundary (proximity_breach)
- Stub backends: drop-in replacements for CI without model weights or hardware

## Repository structure

```
governed-edge-ai/
├── Makefile                       # make smoke | test | lint | typecheck | security | qa
├── docs/
│   ├── architecture.md            # Complete architecture and functional specification
│   ├── deployment-guide.md        # Step-by-step build, from bare metal, for a first-time reader
│   ├── architecture-reconciliation.md  # Published design vs codebase: deltas and rationale
│   ├── build-log.md               # Step-by-step design decisions and QA results
│   ├── ipc-protocol.md            # IPC binary protocol reference, both links
│   ├── governance-mapping.md      # Framework control mapping
│   ├── state-of-play.md           # Current facts, for anyone writing about the project
│   └── release-notes.md           # Release bodies for v1.0.0, v2.0.0 and v3.0.0
├── alvik-firmware/
│   ├── ipc_codec.py               # MicroPython IPC codec (CPython-testable)
│   ├── motor_map.py               # IPC action code to motor API mapping
│   ├── main.py                    # Alvik governance firmware
│   └── pyproject.toml
├── r4-supervisor/                 # Arduino UNO R4 WiFi oversight firmware (C++)
│   ├── r4_supervisor.ino          # Sketch: pins, LED matrix, serial, optional Wi-Fi console
│   ├── supervisor_state.h/.cpp    # The state machine. No Arduino headers: host-compilable.
│   ├── latch.h/.cpp               # Latch relay driver, hardware injected as function pointers
│   ├── ipc_frame.h/.cpp           # IPC codec, oversight subset only
│   ├── test/parity_harness.cpp    # Host driver used by the parity test suite
│   └── README.md
├── audit-service/
│   ├── logger.py                  # AuditLogger, AuditEvent, session management
│   ├── dashboard/                 # FastAPI read-only audit dashboard
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── tests/                     # 99 tests
└── linux-stack/
    ├── ipc/                       # Binary IPC codec + MockSTM32H5
    ├── perception/                # DetectionResult, backends, capture, TCP transport, UNO Q service
    ├── governance/                # GovernanceFilter, VENTUNO Q service
    ├── oversight/                 # Attestation chain, SupervisorLink, latch relay, MockR4Supervisor
    ├── requirements.txt
    ├── pyproject.toml
    └── tests/                     # 604 tests
```

## QA baseline

703 tests across two modules · 100% line coverage on both · ruff clean · mypy strict clean · bandit clean · pip-audit clean

| Module | Tests | Coverage |
|---|---|---|
| linux-stack | 604 | 100% |
| audit-service | 99 | 100% |

```bash
make qa        # lint + typecheck + security + full test suite
make smoke     # fast sanity pass, hardware-free
```

All tests run without physical hardware. CI reproduces the full stack: synthetic camera frames, a pty-based mock STM32H5, a pty-based mock oversight node, and loopback TCP transport. The mocks are real implementations of their state machines rather than stubs, so the path exercised in CI is the one that runs on the rig.

Two suites are worth calling out.

**Adversarial security tests** (`test_security_oversight.py`) attack the design from four positions: a compromised governance host, an attacker on the oversight cable, a compromised host with database write access, and hostile input on either link. They assert what holds *and* what does not. A forged `OVERRIDE_CLEAR` really does release the soft veto, and the test says so; the relay contact is unaffected, which is why there are two paths. A control whose limits are undocumented is a control nobody can rely on.

Three real defects were found this way rather than by review. A missing frame-length guard let one hostile header wedge a link permanently. A failed transmit left the audit log claiming a command had been sent. And the original GPIO kill line **failed open on power loss**, which no test could have caught because the mocks modelled a state machine and had no power to lose. All three are fixed; the third caused the redesign in step 11 and its regression test now models a contact rather than a boolean.

**Firmware parity tests** (`test_r4_firmware_parity.py`) compile the R4's C++ state machine for the host with `-Wall -Wextra -Werror` and check it against the Python reference model: byte-identical frames, identical verdict sequences, identical state transitions, identical constants. Two implementations of one state machine drift unless something checks them.

What is not tested: the Arduino hardware layer itself. Pin timing, the LED matrix driver, Wi-Fi, serial throughput at 921600 baud and the electrical behaviour of the relay contact and its sense line all need the physical rig. `docs/architecture.md` section 12 lists them explicitly.

## Getting started

Without any hardware, in about twenty minutes:

```bash
git clone https://github.com/thierrysays/governed-edge-ai.git
cd governed-edge-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -r linux-stack/requirements.txt -r audit-service/requirements.txt
make qa
```

Then run the whole stack against the mock peers:

```bash
cd linux-stack
PYTHONPATH=../audit-service python3 -m governance.ventuno_q_service \
    --alvik mock --supervisor mock --db /tmp/audit.db
```

With the boards on the bench, **`docs/deployment-guide.md`** takes it from an unconfigured machine to a verified rig: bill of materials, flashing both firmwares, wiring the latch relay into the motor supply, systemd units, and a nine-test procedure for checking that the governance controls actually work. It assumes no prior embedded experience.

## Author

Thierry Sayegh-Sauvage, Glossolalie Advisory. Enterprise architecture and technology governance practitioner (Accenture Technology Consulting, Motorola, RELX Group). This is a first embedded systems build. The point is to test the governance argument in hardware, not to present embedded engineering expertise that does not exist.

## Licence

Code: [Apache 2.0](LICENSE)
Hardware design files (when added): CERN OHL-P v2
Documentation: CC BY 4.0
