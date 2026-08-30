# Linux Stack

Python AI pipeline running on the Qualcomm Dragonwing IQ-8275 NPU side of the Arduino VENTUNO Q.

## Responsibilities

- Object detection (YOLO-X)
- Gesture recognition (MediaPipe)
- Pose estimation and proximity safety boundary (PoseNet)
- Writing every inference event to the audit log before sending any command request
- Folding every stored audit row into the attestation hash chain and publishing the head to the oversight node before the command frame is written
- Polling the oversight node before dispatching, and honouring its veto
- Sending command requests to the STM32 via IPC

## What this stack may not do

This stack has no direct path to actuators. It sends command requests to the STM32 real-time co-processor, which decides whether to execute them.

It also cannot stand its own supervisor down. The oversight node on the UNO R4 WiFi accepts no instruction from here: the protocol contains no message that clears an override, and the relay contact the R4 holds in the Alvik's motor supply is not reachable from any software on this board. This board may ask for the contact to be opened, which the R4 always honours; asking for it to be closed is refused while an override stands. Silence from the oversight node is treated as a veto rather than as consent.

Neither is an implementation detail to be optimised away. They are the governance constraint.

## Inference runtime

All inference runs locally via the Qualcomm AI Hub runtime. No cloud inference dependency at runtime. Edge Impulse is used at development and training time only and is not in the runtime path.

## Error reporting (optional, off by default)

`observability.py` wires the UNO Q perception service and the VENTUNO Q governance service to Sentry for exception tracking, but only if `SENTRY_DSN` is set in the environment. Unset, `init_sentry()` is a no-op and neither service makes an outbound call, preserving the "no cloud dependency at runtime" posture above. To opt in:

| Variable | Default | Purpose |
|---|---|---|
| `SENTRY_DSN` | unset (disabled) | Sentry project DSN. Setting this is what turns reporting on. |
| `SENTRY_ENVIRONMENT` | `development` | Environment tag on reported events. |
| `SENTRY_RELEASE` | unset | Release tag on reported events. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0` | Performance trace sampling; 0 means errors only. |

`send_default_pii` is always forced off: this stack sits on the governance/actuation path, and nothing about detections, commands or audit rows should leave the LAN as a side effect of error reporting.

## Structure

```
linux-stack/
  perception/       DetectionResult, backends, capture, TCP transport, UNO Q service
  ipc/              Binary IPC codec and MockSTM32H5
  governance/       GovernanceFilter and the VENTUNO Q service entry point
  oversight/        Attestation chain, SupervisorLink, MockR4Supervisor
  observability.py  Optional, opt-in Sentry error reporting
  tests/            646 tests, 100% line coverage, hardware-free
  requirements.txt
```

## Status

Implemented and tested. `make linux-test` runs the suite; `make qa` runs the full gate. All tests are hardware-free: the two microcontroller peers are modelled over Unix pseudo-terminals.
