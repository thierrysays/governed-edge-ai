# Linux Stack

Python AI pipeline running on the Qualcomm Dragonwing IQ-8275 NPU side of the Arduino VENTUNO Q.

## Responsibilities

- Object detection (YOLO-X)
- Gesture recognition for the human override channel (MediaPipe)
- Pose estimation and proximity safety boundary (PoseNet)
- Natural-language query interface over the audit log (local LLM)
- Writing every inference event to the audit log before sending any command request
- Sending command requests to the STM32H5 via IPC

## What this stack may not do

This stack has no direct path to actuators. It sends command requests to the STM32H5 real-time co-processor. The STM32H5 decides whether to execute them. This is not an implementation detail to be optimised away; it is the governance constraint.

## Inference runtime

All inference runs locally via the Qualcomm AI Hub runtime. No cloud inference dependency at runtime. Edge Impulse is used at development and training time only and is not in the runtime path.

## Planned structure

```
linux-stack/
  perception/       YOLO-X, MediaPipe, PoseNet wrappers
  ipc/              IPC client: sends command requests to STM32H5
  audit/            Writes inference events to the audit log
  llm_query/        Local LLM interface over the audit database
  requirements.txt
```

## Status

Skeleton pending official VENTUNO Q pinout confirmation.
