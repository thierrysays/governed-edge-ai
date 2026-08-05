# governed-edge-ai

Reference architecture demonstrating that AI governance principles can be enforced in circuitry, not merely documented as policy.

The board is an Arduino VENTUNO Q: Qualcomm Dragonwing IQ-8275 NPU (40 TOPS) on the Linux side for perception and inference; STM32H5 real-time co-processor for actuation and kill-switch logic. A relay wired to the STM32H5 cuts actuator power independently of any software state. The AI pipeline can recommend; it cannot execute.

This project began with the Arduino 21st-anniversary VENTUNO Q announcement and giveaway. It is the first embedded project from Glossolalie Advisory, and the premise is explicit: the governance frameworks argued for in boardrooms should survive contact with hardware.

## Why this matters

Governance frameworks tend to live in documents. They prescribe human oversight, segregation of duties, and auditability as policy objectives. This project treats those same objectives as engineering constraints: the NPU never has a direct path to actuators, every inference-to-actuation pair is written to an append-only audit log, and the kill switch is physically wired, not software-controlled.

The result is a reference architecture that a governance professional can point at and say: this is what a control objective looks like when implemented, not merely stated.

## Non-negotiable design principle: authority separation

The AI perception stack (Linux side) may recommend or request an action. It must never have the sole path to execute one. Every actuation command passes through the STM32H5 real-time core, and the kill switch must remain physically wired to cut actuator power independent of any software state.

This is not a prototyping convenience to be refactored later. It is the governance argument.

## Hardware

| Component | Role |
|---|---|
| Arduino VENTUNO Q (Qualcomm IQ-8275 NPU, 40 TOPS) | Linux side: perception and inference |
| Arduino VENTUNO Q (STM32H5 co-processor) | Real-time actuation control and kill-switch logic |
| PWM-servo robotic arm, 4-6 DOF (model TBD) | Actuator under governance |
| Arduino Modulino Motors | Servo driving |
| Relay module, 5V, 1-channel | Hardware cut of actuator power on kill-switch trigger |
| Raspberry Pi Camera Module 3 (MIPI-CSI) | Primary vision input |
| USB webcam (UVC) | Fallback vision input |
| Emergency stop button, NC, 22 mm | Wired to STM32H5 GPIO, independent of Linux stack |
| Arduino Modulino LED Matrix | Visual status: armed / logging / halted |
| Dedicated NVMe SSD | Audit log storage, separate from OS and model storage |

GPIO and camera connector details are provisional pending the official VENTUNO Q pinout.

## Software stack

### Linux side (Python)

- YOLO-X: object detection
- MediaPipe: gesture recognition (human override channel)
- PoseNet: pose estimation and proximity safety boundary
- Local LLM: natural-language query interface over the audit log
- Qualcomm AI Hub runtime: NPU-accelerated inference, on-device only
- Edge Impulse: development and training time only, not in the runtime path

### STM32H5 side (C, Zephyr + Arduino Core)

- Real-time actuation control loop
- Kill-switch GPIO logic, independent of Linux process state
- Sub-millisecond response budget for control commands

### Audit and telemetry

- SQLite database on the dedicated NVMe SSD
- Append-only log: timestamp, detection type and label, confidence score, command issued, STM32H5 acknowledgement, actor (AI or human override)
- Lightweight Python web service exposing the log via Wi-Fi 6, local network only

## Governance controls implemented

| Control objective | Framework reference | Implementation |
|---|---|---|
| Human oversight and override authority | ISO 42001, COBIT APO | Gesture-triggered halt via STM32H5, independent of AI pipeline |
| Auditability of automated decisions | ISO 42001 | SQLite audit log: every inference-to-actuation pair recorded |
| Segregation of duties (recommend vs execute) | COBIT, TOGAF governance layer | NPU/Linux has no direct actuator access; all commands pass through STM32H5 |
| Model monitoring and drift detection | ISO 42001 | Confidence-score tracking over time, flagged in audit log |
| Data minimisation and sovereignty | ISO 42001, GDPR-adjacent | All inference local; no cloud dependency at runtime |

Full mapping with implementation evidence: [docs/governance-mapping.md](docs/governance-mapping.md)

## Repository structure

```
/linux-stack/        Python AI pipeline (detection, pose, gesture, LLM query interface)
/rt-control/         C / Zephyr for STM32H5 (actuation, kill switch)
/audit-service/      SQLite schema, logging service, dashboard backend
/dashboard/          Operator dashboard (local network only)
/docs/
  governance-mapping.md   Control objectives mapped to implementation
  build-log.md            Running log for the Glossolalie Advisory case study
```

## Open decisions

- Confirm VENTUNO Q pinout (GPIO, MIPI-CSI connector) once published
- Finalise robotic arm model (budget vs degrees of freedom trade-off)
- Confirm board power draw under sustained NPU load; finalise PSU wattage
- Decide industrial vs hobby-grade emergency stop button
- Decide PCA9685 vs Modulino Motors for servo driving (default: Modulino Motors)

## Community commitments

This repository is one of four deliverables from the VENTUNO Q contest entry:

1. Written case study, Glossolalie Advisory site
2. Companion LinkedIn Pulse article in the C-suite governance series
3. Retour d'expérience to Réseau Daubigny (STAR-format case study)
4. This public repository, including audit-logging service and STM32H5 control logic

## Author

Thierry Sayegh-Sauvage, Glossolalie Advisory. Enterprise architecture and technology governance practitioner (Accenture Technology Consulting, Motorola, RELX Group). This is a first embedded systems build. The point is to test the governance argument in hardware, not to present embedded engineering expertise that does not exist.

## Licence

Code: [Apache 2.0](LICENSE)  
Hardware design files (when added): CERN OHL-P v2  
Documentation: CC BY 4.0
