# governed-edge-ai

Reference architecture demonstrating that AI governance principles can be enforced in circuitry, not merely documented as policy.

The stack runs across three Arduino boards: a UNO Q 4GB captures camera frames and runs the perception pipeline; a VENTUNO Q holds the governance filter and audit log; an Alvik mobile robot receives governance-approved commands and executes them. No actuation occurs without a prior audit log entry: enforced at the IPC protocol layer, not in policy.

This project began with the Arduino VENTUNO Q announcement. It is the first embedded project from Glossolalie Advisory, and the premise is explicit: the governance frameworks argued for in boardrooms should survive contact with hardware.

## Why this matters

Governance frameworks tend to live in documents. They prescribe human oversight, segregation of duties, and auditability as policy objectives. This project treats those same objectives as engineering constraints: the NPU never has a direct path to actuators, every inference-to-actuation pair is written to an append-only audit log before the command is transmitted, and the STM32H5 rejects any command that does not carry a valid audit reference.

The result is a reference architecture that a governance professional can point at and say: this is what a control objective looks like when implemented, not merely stated.

## Non-negotiable design principle: authority separation

The AI perception stack (Linux side) may recommend or request an action. It must never have the sole path to execute one. Every actuation command passes through the STM32H5 real-time core, which enforces its own independent confidence gate and rejects commands without a confirmed audit reference.

This is not a prototyping convenience to be refactored later. It is the governance argument.

## Hardware

Three boards, all owned. No external actuator hardware required beyond the Alvik's built-in motors.

| Board | Role | Key specs |
|---|---|---|
| Arduino UNO Q 4GB | Perception node | Qualcomm QRB2210, dual ISP 13 MP / 30 fps, STM32U585, 4 GB LPDDR4, Wi-Fi 5 |
| Arduino VENTUNO Q | Governance brain | Qualcomm IQ-8275 NPU 40 TOPS, STM32H5, 16 GB RAM |
| Arduino Alvik | Physical body | ESP32-S3 + STM32F411, wheeled robot, ToF 8×8, 6-axis IMU, line follower |

**Still to source:** camera module(s) compatible with UNO Q 4GB ISP, USB-C interconnect cables, power supplies.

## Architecture

```
UNO Q 4GB          VENTUNO Q              Alvik
────────────        ────────────────────   ──────────────────
Camera (ISP)  ───►  GovernanceFilter  ───►  STM32F411
Detection          AuditLogger (SQLite)    Motors / ToF / IMU
(YOLO, Pose,       STM32H5 dual gate      CommandAck / Reject
 Gesture)          audit_ref >= 1 enforced
```

Data flow: UNO Q 4GB captures frames → perception pipeline produces `DetectionResult` objects → sent to VENTUNO Q over TCP → `GovernanceFilter` logs to SQLite, gates on confidence, transmits audited `CommandRequest` → Alvik STM32F411 validates `audit_ref`, executes motor command, returns `CommandAck` or `CommandReject`.

## Governance controls implemented

| Control objective | Framework reference | Implementation |
|---|---|---|
| Log-before-act | ISO 42001 Clause 9.1 | `audit_ref` (SQLite rowid >= 1) obtained before any command frame is transmitted |
| No actuation without audit | ISO 42001, NIST AI RMF GOVERN | STM32H5 rejects `CommandRequest` with `audit_ref = 0` at the protocol layer |
| Confidence gate (dual-layer) | Defence-in-depth | Linux gate at 0.70 float64; STM32H5 gate at 0.70 float32, independent enforcement |
| One command per frame | Segregation of duties | Highest-confidence detection only; all others logged as suppressed |
| Full suppression record | Auditability | Every detection logged regardless of whether a command was sent |
| ACK/REJECT tracking | ISO 42001 monitoring | `stm32_ack` column updated after MCU response; NULL on timeout (forensically meaningful) |
| Human override authority | ISO 42001, COBIT APO | Gesture-triggered HALT via perception pipeline; kill-switch state machine on STM32H5 |

## Software stack

All eight build steps are complete.

| Module | Location | Description |
|---|---|---|
| IPC codec | `linux-stack/ipc/codec.py` | Binary protocol, 8 message types, CRC-16/CCITT, `FrameParser` |
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
│   ├── build-log.md               # Step-by-step design decisions and QA results
│   ├── ipc-protocol.md            # IPC binary protocol reference
│   ├── governance-mapping.md      # Framework control mapping
│   └── cowork-bom-arduino.md      # Peripherals BOM (cameras, cables, power)
├── alvik-firmware/
│   ├── ipc_codec.py               # MicroPython IPC codec (CPython-testable)
│   ├── motor_map.py               # IPC action code to motor API mapping
│   ├── main.py                    # Alvik governance firmware
│   └── pyproject.toml
├── audit-service/
│   ├── logger.py                  # AuditLogger, AuditEvent, session management
│   ├── dashboard/                 # Flask read-only audit dashboard
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── tests/                     # 148 tests
└── linux-stack/
    ├── ipc/                       # Binary IPC codec + MockSTM32H5
    ├── perception/                # DetectionResult, backends, capture, TCP transport, UNO Q service
    ├── governance/                # GovernanceFilter, VENTUNO Q service
    ├── requirements.txt
    ├── pyproject.toml
    └── tests/                     # 241 tests
```

## QA baseline

313 tests total across two modules · ruff clean · mypy clean · bandit clean · pip-audit clean

| Module | Tests | Coverage |
|---|---|---|
| linux-stack | 241 | 95.76% |
| audit-service | 72 | 96.13% |

```bash
make qa        # lint + typecheck + security + full test suite
make smoke     # fast sanity pass, hardware-free
```

All tests run without physical hardware. CI reproduces the full stack: synthetic camera frames, pty-based mock STM32H5, loopback TCP transport.

## Author

Thierry Sayegh-Sauvage, Glossolalie Advisory. Enterprise architecture and technology governance practitioner (Accenture Technology Consulting, Motorola, RELX Group). This is a first embedded systems build. The point is to test the governance argument in hardware, not to present embedded engineering expertise that does not exist.

## Licence

Code: [Apache 2.0](LICENSE)
Hardware design files (when added): CERN OHL-P v2
Documentation: CC BY 4.0
