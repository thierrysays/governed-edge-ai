# governed-edge-ai

Reference architecture demonstrating that AI governance principles can be enforced in circuitry, not merely documented as policy.

The stack runs across four Arduino boards. A UNO Q 4GB captures camera frames and runs the perception pipeline. A VENTUNO Q holds the governance filter and audit log. An Alvik mobile robot receives governance-approved commands and executes them. A UNO R4 WiFi sits outside that chain, watches the governance tier, and can stop it.

No actuation occurs without a prior audit log entry, and no audit log entry goes unwitnessed by a board the governance host does not control. Both are enforced at the protocol and GPIO layer, not in policy.

This project began with the Arduino VENTUNO Q announcement. It is the first embedded project from Glossolalie Advisory, and the premise is explicit: the governance frameworks argued for in boardrooms should survive contact with hardware.

## Why this matters

Governance frameworks tend to live in documents. They prescribe human oversight, segregation of duties, and auditability as policy objectives. This project treats those same objectives as engineering constraints: the NPU never has a direct path to actuators, every inference-to-actuation pair is written to an append-only audit log before the command is transmitted, the STM32 rejects any command that does not carry a valid audit reference, and a board outside the whole chain holds the evidence and the stop button.

The result is a reference architecture that a governance professional can point at and say: this is what a control objective looks like when implemented, not merely stated. It also shows what the implementation costs, and where it stops working, which the documents rarely do.

## Non-negotiable design principle: authority separation

The AI perception stack (Linux side) may recommend or request an action. It must never have the sole path to execute one. Every actuation command passes through the STM32 real-time core, which enforces its own independent confidence gate and rejects commands without a confirmed audit reference.

A second principle was added with the oversight node, and it corrects a real weakness in the three-board version: **the function that supervises a system must not depend on that system.** The human override used to live inside the stack it was meant to override. The gesture HALT travelled through the AI perception pipeline; the kill switch sat on the actuation MCU that the governance node itself commands. Both were real controls, and both went down with the thing they supervised.

The UNO R4 WiFi is not on the command path, is reached by its own link, and holds a GPIO line into the robot that no message on any link can reach. Nothing the governance tier can send releases it: the protocol contains no such message, and the test suite proves it by throwing the entire outbound vocabulary at a latched node.

Neither principle is a prototyping convenience to be refactored later. They are the governance argument.

## Hardware

Four boards, all owned. No external actuator hardware required beyond the Alvik's built-in motors.

| Board | Role | Key specs |
|---|---|---|
| Arduino UNO Q 4GB | Perception node | Qualcomm QRB2210, dual ISP 13 MP / 30 fps, STM32U585, 4 GB LPDDR4, Wi-Fi 5 |
| Arduino VENTUNO Q | Governance brain | Qualcomm IQ-8275 NPU 40 TOPS, STM32H5, 16 GB RAM |
| Arduino Alvik | Physical body | ESP32-S3 + STM32F411, wheeled robot, ToF 8×8, 6-axis IMU, line follower |
| Arduino UNO R4 WiFi | Oversight node | Renesas RA4M1 + ESP32-S3, 12x8 LED matrix, 32 KB SRAM |

The R4 is the least capable board here, and that is the point. The whole oversight firmware is a few hundred lines of C++ with no scheduler, no filesystem and no network stack in the default build: small enough to read in one sitting, which is what a supervisor should be.

**Still to source:** camera module(s) compatible with UNO Q 4GB ISP, USB-C interconnect cables, power supplies, two momentary buttons and four jumper wires for the oversight node.

## Architecture

```
                        UNO R4 WiFi
                        ────────────────────────────
                        Oversight node (Tier 0)
                        Override button, LED matrix
                        64 retained audit digests
                        ────────────────────────────
                          ▲                     │
       heartbeat + audit  │                     │  soft veto
       chain digests      │                     ▼
UNO Q 4GB          VENTUNO Q              Alvik
────────────        ────────────────────   ──────────────────
Camera (ISP)  ───►  GovernanceFilter  ───►  STM32F411
Detection          AuditLogger (SQLite)    Motors / ToF / IMU
(YOLO, Pose,       Attestation chain       CommandAck / Reject
 Gesture)          audit_ref >= 1 enforced  kill-switch pin
                                                   ▲
                          hard kill line, GPIO     │
                          no protocol in the path  │
                        ───────────────────────────┘
```

Data flow: UNO Q captures frames, the perception pipeline produces `DetectionResult` objects, these go to the VENTUNO Q over TCP. The `GovernanceFilter` asks the oversight node whether it may act, logs every detection to SQLite, folds the stored row into a SHA-256 hash chain and publishes the head to the R4, then transmits an audited `CommandRequest`. The Alvik validates `audit_ref` and its own kill-switch pin, executes, and returns `CommandAck` or `CommandReject`.

The digest reaches the oversight node before the command reaches the robot. That ordering is what turns the retained digests into evidence rather than a log of a log.

## Governance controls implemented

| Control objective | Framework reference | Implementation |
|---|---|---|
| Log-before-act | ISO 42001 Clause 9.1 | `audit_ref` (SQLite rowid >= 1) obtained before any command frame is transmitted |
| No actuation without audit | ISO 42001, NIST AI RMF GOVERN | STM32H5 rejects `CommandRequest` with `audit_ref = 0` at the protocol layer |
| Confidence gate (dual-layer) | Defence-in-depth | Linux gate at 0.70 float64; STM32H5 gate at 0.70 float32, independent enforcement |
| One command per frame | Segregation of duties | Highest-confidence detection only; all others logged as suppressed |
| Full suppression record | Auditability | Every detection logged regardless of whether a command was sent |
| ACK/REJECT tracking | ISO 42001 monitoring | `stm32_ack` column updated after MCU response; NULL on timeout (forensically meaningful) |
| Human override authority | ISO 42001, COBIT APO | Physical NC button on the UNO R4 WiFi, outside the AI stack entirely. Latches; no protocol message clears it. |
| Independent oversight function | ISO 42001 Clause 9.2, COBIT MEA02 | Tier 0 is not on the command path, uses its own link, and enforces through a GPIO line no message can reach |
| Witness-before-act | ISO 42001 Clause 9.1 | SHA-256 audit chain head published to the oversight node before the command frame is written |
| Off-host tamper evidence | ISO 42001 Clause 9.1, NIST AI RMF MEASURE | 64 chain digests retained on a board the governance host does not control. Rewriting the SQLite log is detected on reconciliation. |
| Governance liveness | COBIT DSS02 | The oversight node latches an override if the governance tier stops reporting for 2 s. Restarting the software does not clear it. |
| Fail-closed on oversight loss | ISO 42001 Clause 8.4 | Silence from the oversight node counts as a veto. A supervisor that cannot be reached is not a satisfied supervisor. |

## Software stack

All nine build steps are complete.

| Module | Location | Description |
|---|---|---|
| IPC codec | `linux-stack/ipc/codec.py` | Binary protocol, 13 message types across two links, CRC-16/CCITT, `FrameParser` |
| Mock STM32H5 | `linux-stack/ipc/mock_peer.py` | Pty-based hardware simulator, full state machine, kill-switch |
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
| Alvik firmware | `alvik-firmware/main.py` | Four governance gates, kill-switch GPIO, USB-C serial I/O |
| Audit attestation | `linux-stack/oversight/attestation.py` | SHA-256 chain over audit rows, offline tamper reconciliation |
| Supervisor link | `linux-stack/oversight/supervisor_link.py` | VENTUNO Q side of the oversight link, fail-closed on loss |
| Mock oversight node | `linux-stack/oversight/mock_supervisor.py` | Pty-based R4 model. The executable specification for the firmware. |
| R4 oversight firmware | `r4-supervisor/` | Arduino C++: state machine, IPC subset, LED matrix, kill line |

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
│   ├── build-log.md               # Step-by-step design decisions and QA results
│   ├── ipc-protocol.md            # IPC binary protocol reference, both links
│   ├── governance-mapping.md      # Framework control mapping
│   └── cowork-bom-arduino.md      # Peripherals BOM (cameras, cables, power, oversight parts)
├── alvik-firmware/
│   ├── ipc_codec.py               # MicroPython IPC codec (CPython-testable)
│   ├── motor_map.py               # IPC action code to motor API mapping
│   ├── main.py                    # Alvik governance firmware
│   └── pyproject.toml
├── r4-supervisor/                 # Arduino UNO R4 WiFi oversight firmware (C++)
│   ├── r4_supervisor.ino          # Sketch: pins, LED matrix, serial, optional Wi-Fi console
│   ├── supervisor_state.h/.cpp    # The state machine. No Arduino headers: host-compilable.
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
    ├── oversight/                 # Attestation chain, SupervisorLink, MockR4Supervisor
    ├── requirements.txt
    ├── pyproject.toml
    └── tests/                     # 512 tests
```

## QA baseline

611 tests across two modules · 100% line coverage on both · ruff clean · mypy strict clean · bandit clean · pip-audit clean

| Module | Tests | Coverage |
|---|---|---|
| linux-stack | 512 | 100% |
| audit-service | 99 | 100% |

```bash
make qa        # lint + typecheck + security + full test suite
make smoke     # fast sanity pass, hardware-free
```

All tests run without physical hardware. CI reproduces the full stack: synthetic camera frames, a pty-based mock STM32H5, a pty-based mock oversight node, and loopback TCP transport. The mocks are real implementations of their state machines rather than stubs, so the path exercised in CI is the one that runs on the rig.

Two suites are worth calling out.

**Adversarial security tests** (`test_security_oversight.py`) attack the design from four positions: a compromised governance host, an attacker on the oversight cable, a compromised host with database write access, and hostile input on either link. They assert what holds *and* what does not. A forged `OVERRIDE_CLEAR` really does release the soft veto, and the test says so; the hard kill line is unaffected, which is why there are two paths. A control whose limits are undocumented is a control nobody can rely on.

Two real defects were found this way rather than by review: a missing frame-length guard that let one hostile header wedge a link permanently, and a failed transmit that left the audit log claiming a command had been sent. Both are fixed, both have regression tests.

**Firmware parity tests** (`test_r4_firmware_parity.py`) compile the R4's C++ state machine for the host with `-Wall -Wextra -Werror` and check it against the Python reference model: byte-identical frames, identical verdict sequences, identical state transitions, identical constants. Two implementations of one state machine drift unless something checks them.

What is not tested: the Arduino hardware layer itself. Pin timing, the LED matrix driver, Wi-Fi, serial throughput at 921600 baud and the electrical behaviour of the kill line all need the physical rig. `docs/architecture.md` section 12 lists them explicitly.

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

With the boards on the bench, **`docs/deployment-guide.md`** takes it from an unconfigured machine to a verified four-board rig: bill of materials, flashing both firmwares, wiring the kill line, systemd units, and a seven-test procedure for checking that the governance controls actually work. It assumes no prior embedded experience.

## Author

Thierry Sayegh-Sauvage, Glossolalie Advisory. Enterprise architecture and technology governance practitioner (Accenture Technology Consulting, Motorola, RELX Group). This is a first embedded systems build. The point is to test the governance argument in hardware, not to present embedded engineering expertise that does not exist.

## Licence

Code: [Apache 2.0](LICENSE)
Hardware design files (when added): CERN OHL-P v2
Documentation: CC BY 4.0
