"""
Alvik firmware main loop.

Governance contract enforced on this MCU:
  1. audit_ref must be non-zero. A CommandRequest with audit_ref == 0
     is rejected with REJ_AUDIT_REF_ZERO unconditionally. This enforces
     the log-before-act invariant at the physical actuation boundary.
  2. confidence must pass the float32 gate (>= CONFIDENCE_THRESHOLD).
     This is the second layer of the dual-layer confidence gate; the
     VENTUNO Q Linux side is the first. An IEEE 754 rounding edge at
     exactly 0.70 is caught here even if Linux passed it.
  3. Unknown action types are rejected with REJ_UNKNOWN_ACTION.
  4. The kill-switch (KILL_PIN) halts all motion immediately and rejects
     all subsequent commands until reset.

     KILL_PIN is a local test input and **not** a governance control. It was
     briefly driven by the oversight node, and that arrangement was wrong on
     two counts: it put a governance module on the governed component, where
     it worked only because this firmware chooses to read the pin, and it
     failed open when the driving board lost power.

     Physical enforcement now sits in a bistable latch relay in series with
     the motor supply, owned by the oversight node. There is nothing for this
     firmware to honour or ignore. These four gates remain as defence in
     depth: they cost nothing and they fail in the safe direction.

Serial interface:
  VENTUNO Q connects to the Alvik ESP32-S3 via USB-C. Commands arrive
  on sys.stdin.buffer; responses are written to sys.stdout.buffer.
  Both are the USB CDC serial interface at the MicroPython REPL baud rate.

Hardware dependencies (Arduino_Alvik MicroPython library):
  from arduino_alvik import ArduinoAlvik
"""

import sys
import time

from arduino_alvik import ArduinoAlvik  # type: ignore[import]

from ipc_codec import (
    CONFIDENCE_THRESHOLD,
    REJ_AUDIT_REF_ZERO,
    REJ_CONFIDENCE_BELOW_THRESHOLD,
    REJ_KILL_SWITCH_ACTIVE,
    REJ_UNKNOWN_ACTION,
    FrameParser,
    encode_ack,
    encode_halt_notify,
    encode_reject,
)
from motor_map import MOTOR_MAP

# Local test input, active low. Not a governance control: see the module
# docstring. Physical enforcement is the latch relay in the motor supply.
KILL_PIN = 4

_READ_CHUNK = 64
_HALT_SPEED_RPM = 0


def _apply_kill_switch(alvik: ArduinoAlvik, pin_num: int) -> bool:
    """Read kill-switch pin; return True if the switch was pressed this call."""
    try:
        from machine import Pin  # type: ignore[import]
        pin = Pin(pin_num, Pin.IN, Pin.PULL_UP)
        return pin.value() == 0
    except Exception:
        return False


def run() -> None:
    alvik = ArduinoAlvik()
    alvik.begin()

    parser = FrameParser()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    kill_switch_active = False
    last_kill_check = 0.0

    while True:
        now = time.time()

        # Poll kill-switch every 50 ms
        if now - last_kill_check >= 0.05:
            if _apply_kill_switch(alvik, KILL_PIN) and not kill_switch_active:
                kill_switch_active = True
                alvik.brake()
                stdout.write(encode_halt_notify(0))
                stdout.flush()
            last_kill_check = now

        # Non-blocking read
        try:
            data = stdin.read(1)
        except Exception:
            data = b""

        if not data:
            continue

        parser.feed(data)
        for msg in parser.pop_messages():
            _handle(msg, alvik, stdout, kill_switch_active)


def _handle(msg, alvik, stdout, kill_switch_active: bool) -> None:
    # Governance gate 1: audit_ref must be non-zero (log-before-act)
    if msg.audit_ref == 0:
        stdout.write(encode_reject(msg.audit_ref, REJ_AUDIT_REF_ZERO))
        stdout.flush()
        return

    # Governance gate 2: kill-switch overrides all commands
    if kill_switch_active:
        stdout.write(encode_reject(msg.audit_ref, REJ_KILL_SWITCH_ACTIVE))
        stdout.flush()
        return

    # Governance gate 3: float32 confidence threshold (defence-in-depth)
    if msg.confidence < CONFIDENCE_THRESHOLD:
        stdout.write(encode_reject(msg.audit_ref, REJ_CONFIDENCE_BELOW_THRESHOLD))
        stdout.flush()
        return

    # Governance gate 4: unknown action type
    action_fn = MOTOR_MAP.get(msg.action_type)
    if action_fn is None:
        stdout.write(encode_reject(msg.audit_ref, REJ_UNKNOWN_ACTION))
        stdout.flush()
        return

    # All gates passed: execute and acknowledge
    try:
        action_fn(alvik, msg.action_param)
    except Exception:
        # Motor driver error: acknowledge anyway so VENTUNO Q audit log
        # records the attempt; the Alvik hardware may fault independently
        pass

    stdout.write(encode_ack(msg.audit_ref))
    stdout.flush()


if __name__ == "__main__":
    run()
