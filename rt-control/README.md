# Real-time Control (STM32H5)

C code for the STM32H5 real-time co-processor, targeting Zephyr RTOS with Arduino Core overlay.

**Status note.** This directory is a placeholder from the original three-board plan and remains unwritten. Two things overtook it. The actuation-side real-time gates are implemented today on the Alvik's STM32F411, in `alvik-firmware/`, because that is where the motors are. The status indicator and the kill-switch authority moved to the Arduino UNO R4 WiFi oversight node in `r4-supervisor/`, because a status light driven by the board it reports on is a status light nobody should trust.

What remains genuinely for this directory is whatever the VENTUNO Q's own STM32H5 should do once its pinout is published. That is still open.

## Responsibilities

- Receive command requests from the Linux side via IPC
- Enforce kill-switch logic: NC emergency stop button wired to GPIO cuts the actuator relay independently of Linux process state
- Drive the servo control loop within the sub-millisecond response budget
- Drive a local status indicator (armed / logging / halted). The governance annunciator visible to an operator lives on the oversight node instead: see `r4-supervisor/README.md`.

## Authority model

The STM32H5 is the sole execution authority for all actuation commands. The Linux NPU side sends requests; the STM32H5 decides whether to execute them and may refuse or halt at any time based on kill-switch state, safety bounds, or watchdog timeout.

This design is intentional. Refactoring it into a single-process or software-only safety model defeats the governance argument the project exists to make.

## Planned structure

```
rt-control/
  src/
    main.c            Entry point, Zephyr application
    kill_switch.c     GPIO interrupt handler for emergency stop
    ipc_server.c      Receives command requests from Linux
    servo_control.c   Actuation control loop
    led_status.c      Modulino LED Matrix driver
  CMakeLists.txt
  prj.conf
  boards/             Board-specific Kconfig and DTS overlays
```

## Constraints

- Control loop response budget: < 1 ms
- Kill-switch GPIO must be wired NC (normally closed); open circuit triggers halt
- Pinout provisional until official VENTUNO Q documentation is published

## Status

Skeleton pending official VENTUNO Q pinout confirmation. See the status note above for what has moved elsewhere in the meantime.
