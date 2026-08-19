# Governance Mapping

This document maps each control objective to its implementation in the codebase. Keep it in sync with actual code as the project develops.

## Framework references

| Abbreviation | Full name |
|---|---|
| ISO 42001 | ISO/IEC 42001:2023: AI management systems |
| COBIT APO | COBIT 2019, Align, Plan and Organise domain |
| COBIT DSS | COBIT 2019, Deliver, Service and Support domain |
| TOGAF | The Open Group Architecture Framework, governance layer |

## Control mapping

| Control objective | Framework reference | Implementation | Status |
|---|---|---|---|
| Human oversight and override authority | ISO 42001 §6.1, COBIT APO12 | Gesture-triggered halt via STM32H5 GPIO; halt state independent of AI pipeline state | Pending implementation |
| Auditability of automated decisions | ISO 42001 §9.1 | SQLite append-only log on dedicated NVMe SSD; schema in `audit-service/schema.sql` | Schema defined |
| Segregation of duties: recommend vs execute | COBIT APO01, TOGAF governance layer | NPU/Linux side has no direct I/O path to actuators; all commands pass through STM32H5 IPC channel | Architectural constraint |
| Real-time kill switch, independent of software state | ISO 42001 §8.4, COBIT DSS02 | NC emergency stop button wired to STM32H5 GPIO, relay cuts actuator power line; not software-mediated | Pending hardware assembly |
| Model performance monitoring and drift detection | ISO 42001 §10.2 | Confidence-score time-series written to audit log; flagging thresholds TBD | Pending implementation |
| Data minimisation and local sovereignty | ISO 42001 §8.2, GDPR Art. 5(1)(c) | All inference runs on-device via Qualcomm AI Hub runtime; no outbound telemetry by default | Architectural constraint |
| Visual governance status indicator | ISO 42001 §8.4 | Modulino LED Matrix: armed (green), logging (amber), halted (red) | Pending implementation |
| Operator audit log access | ISO 42001 §9.1 | Local web service exposing SQLite log over Wi-Fi 6, LAN only; no cloud relay | Pending implementation |

## Architecture: authority separation

```
                    +------------------------------------------+
                    |            Linux side (NPU)              |
                    |                                          |
Camera -----------> |  YOLO-X / MediaPipe / PoseNet            |
(MIPI-CSI / USB)   |                                          |
                    |  Inference  -->  Command request         |
                    |                                          |
                    |  Audit logger  (every inference)         |
                    +------------------+-----------------------+
                                       |
                              IPC (serial / shared mem)
                              RECOMMEND only -- never EXECUTE
                                       |
                                       v
                    +------------------------------------------+
                    |         STM32H5 (real-time)              |
                    |                                          |
Kill switch ------> |  Kill-switch GPIO handler                |
(NC button)         |  (independent of Linux process state)   |
                    |                                          |
                    |  Control loop  (< 1 ms budget)           |
                    |                                          |
                    |  Relay driver -----> Relay --> Actuator  |
                    +------------------------------------------+
```

## Open governance questions

- Formalise the IPC protocol between Linux and STM32H5; define what a command request message must contain (source, confidence, timestamp, action type)
- Define confidence-score thresholds that trigger a log flag vs an automatic hold
- Specify the LED Matrix state machine (states, transitions, ownership per state)
- Decide audit log retention policy and whether log rotation is permitted under the append-only constraint
