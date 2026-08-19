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
 Gesture)          audit_ref ≥ 1 enforced
```

Data flow: UNO Q 4GB captures frames → perception pipeline produces `DetectionResult` objects → sent to VENTUNO Q → `GovernanceFilter` logs to SQLite, gates on confidence, transmits audited `CommandRequest` → Alvik STM32F411 validates `audit_ref`, executes motor command, returns `CommandAck` or `CommandReject`.

## Governance controls implemented

| Control objective | Framework reference | Implementation |
|---|---|---|
| Log-before-act | ISO 42001 Clause 9.1 | `audit_ref` (SQLite rowid ≥ 1) obtained before any command frame is transmitted |
| No actuation without audit | ISO 42001, NIST AI RMF GOVERN | STM32H5 rejects `CommandRequest` with `audit_ref = 0` at the protocol layer |
| Confidence gate (dual-layer) | Defence-in-depth | Linux gate at 0.70 float64; STM32H5 gate at 0.70 float32, independent enforcement |
| One command per frame | Segregation of duties | Highest-confidence detection only; all others logged as suppressed |
| Full suppression record | Auditability | Every detection logged regardless of whether a command was sent |
| ACK/REJECT tracking | ISO 42001 monitoring | `stm32_ack` column updated after MCU response; NULL on timeout (forensically meaningful) |
| Human override authority | ISO 42001, COBIT APO | Gesture-triggered HALT via perception pipeline; kill-switch state machine on STM32H5 |

## Software stack

### Built and tested

| Module | Location | Description |
|---|---|---|
| IPC codec | `linux-stack/ipc/codec.py` | Binary protocol, 8 message types, CRC-16/CCITT, `FrameParser` |
| Mock STM32H5 | `linux-stack/ipc/mock_peer.py` | Pty-based hardware simulator, full state machine, kill-switch |
| Perception pipeline | `linux-stack/perception/` | `DetectionResult` dataclass, stub backends, `PerceptionPipeline` ABC |
| Governance filter | `linux-stack/governance/filter.py` | `GovernanceFilter`, `DEFAULT_COMMAND_MAP`, log-before-act enforcement |
| Audit logger | `audit-service/logger.py` | SQLite WAL-mode, append-only, `AuditEvent`, session management |
| Audit dashboard | `audit-service/dashboard/` | Flask read-only dashboard over the audit log |

### Planned (next steps)

| Step | Description |
|---|---|
| Step 7 | Alvik firmware (MicroPython or Arduino C): receive `CommandRequest` via USB-C, execute motor commands, return `CommandAck` / `CommandReject` |
| Step 8 | UNO Q 4GB perception service: camera capture via ISP, run YOLO / MediaPipe / PoseNet, send `DetectionResult` objects to VENTUNO Q over the network |

### Inference models (planned for Step 8)

- YOLO-X: object detection (person, robot_part, tool)
- MediaPipe: gesture recognition (stop, thumbs_up, thumbs_down)
- PoseNet: proximity safety boundary (proximity_breach)
- Qualcomm AI Hub runtime: NPU-accelerated inference on VENTUNO Q

## Repository structure

```
governed-edge-ai/
├── Makefile                    # make smoke | test | lint | typecheck | security | qa
├── docs/
│   ├── build-log.md            # Step-by-step design decisions and QA results
│   ├── cowork-session-summary.md
│   └── cowork-bom-arduino.md   # Peripherals BOM (cameras, cables, power)
├── audit-service/
│   ├── logger.py               # AuditLogger, AuditEvent, session management
│   ├── dashboard/              # Flask read-only audit dashboard
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── tests/                  # 148 tests
└── linux-stack/
    ├── ipc/                    # Binary IPC codec + MockSTM32H5
    ├── perception/             # DetectionResult, stub backends
    ├── governance/             # GovernanceFilter
    ├── requirements.txt
    ├── pyproject.toml
    └── tests/                  # 36 governance tests + smoke suite
```

## QA baseline

184 tests · 97% coverage · ruff clean · mypy clean · bandit clean · pip-audit clean

```bash
make qa        # lint + typecheck + security + full test suite
make smoke     # fast sanity pass, hardware-free
```

## Open decisions

- Camera module for UNO Q 4GB ISP: Arduino native or third-party (Arducam)?
- UNO Q 4GB ↔ VENTUNO Q transport: Wi-Fi (UDP/gRPC) or USB-C UART?
- Alvik firmware language: MicroPython or Arduino C?
- Alvik `CommandRequest` reception: USB-C serial or Bluetooth 5.1?
- Confidence threshold calibration: 0.70 is an engineering default; no published standard maps confidence to injury probability for human-robot collaboration

## Author

Thierry Sayegh-Sauvage, Glossolalie Advisory. Enterprise architecture and technology governance practitioner (Accenture Technology Consulting, Motorola, RELX Group). This is a first embedded systems build. The point is to test the governance argument in hardware, not to present embedded engineering expertise that does not exist.

## Licence

Code: [Apache 2.0](LICENSE)
Hardware design files (when added): CERN OHL-P v2
Documentation: CC BY 4.0
