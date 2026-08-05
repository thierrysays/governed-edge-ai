# Real-time Control (STM32H5)

C code for the STM32H5 real-time co-processor, targeting Zephyr RTOS with Arduino Core overlay.

## Responsibilities

- Receive command requests from the Linux side via IPC
- Enforce kill-switch logic: NC emergency stop button wired to GPIO cuts the actuator relay independently of Linux process state
- Drive the servo control loop within the sub-millisecond response budget
- Drive the Modulino LED Matrix status indicator (armed / logging / halted)

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

Skeleton pending official VENTUNO Q pinout confirmation.
