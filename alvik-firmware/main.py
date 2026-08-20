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

A command that passes all four gates and then raises inside the motor driver
is answered with REJ_SYSTEM_FAULT, not an ACK. `stm32_ack = 1` means accepted
*and executed*, and acknowledging a failed motor call would put a false
assertion of motion into the audit journal.

Serial interface:
  VENTUNO Q connects to the Alvik ESP32-S3 via USB-C. Commands arrive
  on sys.stdin.buffer; responses are written to sys.stdout.buffer.
  Both are the USB CDC serial interface at the MicroPython REPL baud rate.

Hardware dependencies (Arduino_Alvik MicroPython library):
  from arduino_alvik import ArduinoAlvik
"""

import sys
import time

try:
    from arduino_alvik import ArduinoAlvik  # type: ignore[import]
except ImportError:  # pragma: no cover - the library exists only on the board
    # Importable under CPython so the governance gates below can be tested.
    # Every gate decision in `_handle` is pure logic over a decoded message;
    # only the motor call itself touches the library. Leaving this file
    # unimportable is why the gates went untested, and why a failed motor
    # call was acknowledged as a success for two releases.
    ArduinoAlvik = object  # type: ignore[assignment,misc]

from ipc_codec import (
    CONFIDENCE_THRESHOLD,
    REJ_AUDIT_REF_ZERO,
    REJ_CONFIDENCE_BELOW_THRESHOLD,
    REJ_KILL_SWITCH_ACTIVE,
    REJ_SYSTEM_FAULT,
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

    # All gates passed: execute, then acknowledge only what executed.
    #
    # A raise here used to be swallowed and acknowledged anyway, on the
    # reasoning that the audit log should record the attempt. It did record
    # something, and what it recorded was false: `stm32_ack = 1` means the
    # command was accepted *and executed*, so a failed motor call produced a
    # journal entry asserting motion that never happened.
    #
    # That is the same defect the governance filter had at the transmit layer,
    # one board further out, and it is the shape this whole project exists to
    # refuse: the component that was asked to act reporting that it acted.
    # SYSTEM_FAULT is the honest answer. The attempt is still on record,
    # because the row was written before the command was ever sent.
    try:
        action_fn(alvik, msg.action_param)
    except Exception:
        # Caught rather than propagated: a motor driver fault must not take
        # the governance loop down with it. The board stays responsive and
        # says what happened.
        stdout.write(encode_reject(msg.audit_ref, REJ_SYSTEM_FAULT))
        stdout.flush()
        return

    stdout.write(encode_ack(msg.audit_ref))
    stdout.flush()


if __name__ == "__main__":
    run()
