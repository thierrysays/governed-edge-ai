"""
Action type to Alvik motor command mapping.

Maps IPC ActionType codes to Arduino_Alvik MicroPython API calls.
Each command function receives the Alvik instance and action_param (int16).

action_param semantics per action:
  MOVE_FORWARD / MOVE_BACKWARD:  speed as a percentage 0..100 of max RPM
  TURN_LEFT / TURN_RIGHT:        angle in degrees (1..180)
  HALT / STOP_MOTORS:            param ignored
"""

from ipc_codec import (
    ACTION_HALT,
    ACTION_MOVE_BACKWARD,
    ACTION_MOVE_FORWARD,
    ACTION_NONE,
    ACTION_STOP_MOTORS,
    ACTION_TURN_LEFT,
    ACTION_TURN_RIGHT,
)

# Alvik wheel speed in RPM at 100% param
_MAX_RPM = 60

# Alvik rotate speed in degrees/second
_ROTATE_SPEED = 90


def _halt(alvik, param: int) -> None:
    alvik.brake()


def _stop(alvik, param: int) -> None:
    alvik.stop()


def _move_forward(alvik, param: int) -> None:
    speed = max(10, min(100, param))
    rpm = int(_MAX_RPM * speed / 100)
    alvik.set_wheels_speed(rpm, rpm)


def _move_backward(alvik, param: int) -> None:
    speed = max(10, min(100, param))
    rpm = int(_MAX_RPM * speed / 100)
    alvik.set_wheels_speed(-rpm, -rpm)


def _turn_left(alvik, param: int) -> None:
    degrees = max(1, min(180, param))
    alvik.rotate(degrees, _ROTATE_SPEED)


def _turn_right(alvik, param: int) -> None:
    degrees = max(1, min(180, param))
    alvik.rotate(-degrees, _ROTATE_SPEED)


def _noop(alvik, param: int) -> None:
    pass


MOTOR_MAP = {
    ACTION_HALT:          _halt,
    ACTION_STOP_MOTORS:   _stop,
    ACTION_MOVE_FORWARD:  _move_forward,
    ACTION_MOVE_BACKWARD: _move_backward,
    ACTION_TURN_LEFT:     _turn_left,
    ACTION_TURN_RIGHT:    _turn_right,
    ACTION_NONE:          _noop,
}
