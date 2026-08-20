"""
The four governance gates on the Alvik, and what happens after them.

`alvik-firmware/main.py` had no test coverage until a security audit found a
defect in it: a motor call that raised was answered with `CommandAck`, so the
audit journal recorded `stm32_ack = 1`, which means accepted *and executed*,
for motion that never happened. The same shape as the transmit-layer defect
fixed earlier, one board further out.

The gates are pure logic over a decoded message. Only the motor call touches
the Arduino library, so with that import made optional the whole decision path
runs under CPython against a fake board and a fake stdout.
"""

import os
import pathlib
import sys

_FIRMWARE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "alvik-firmware")
sys.path.insert(0, os.path.abspath(_FIRMWARE_DIR))

import ipc_codec as ac  # noqa: E402
import main as fw  # noqa: E402

from ipc.codec import (  # noqa: E402
    ActionType,
    Actor,
    CommandRequest,
    FrameParser,
    RejectReason,
    encode,
)


class FakeStdout:
    """Captures what the firmware would put on the wire."""

    def __init__(self) -> None:
        self.written = b""
        self.flushes = 0

    def write(self, data: bytes) -> None:
        self.written += data

    def flush(self) -> None:
        self.flushes += 1


class FakeAlvik:
    """A board that records what it was asked to do, or refuses to do it."""

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self.raises = raises

    def set_wheels_speed(self, left, right):        # noqa: ANN001, ANN201
        if self.raises:
            raise RuntimeError("motor driver fault")
        self.calls.append(("wheels", (left, right)))

    def __getattr__(self, name):                     # noqa: ANN001, ANN204
        def _call(*args, **kwargs):                  # noqa: ANN002, ANN003, ANN202
            if self.raises:
                raise RuntimeError("motor driver fault")
            self.calls.append((name, args))
        return _call


def _request(**kw) -> object:
    defaults = {
        "timestamp_us": 1_000, "audit_ref": 1, "actor": Actor.AI,
        "action_type": ActionType.MOVE_FORWARD, "action_param": 0,
        "confidence": 0.95,
    }
    defaults.update(kw)
    return CommandRequest(**defaults)


def _decoded(**kw):
    """A message shaped as the Alvik's own codec would hand it to `_handle`."""
    parser = ac.FrameParser()
    parser.feed(encode(_request(**kw)))
    msgs = parser.pop_messages()
    assert len(msgs) == 1
    return msgs[0]


def _reply(out: FakeStdout):
    """Decode with the linux-stack codec: the Alvik's own parser only decodes
    the inbound direction, which is the point of keeping it small."""
    parser = FrameParser()
    parser.feed(out.written)
    msgs = parser.pop_messages()
    assert len(msgs) == 1, f"expected exactly one reply, got {msgs}"
    return msgs[0]


# ---------------------------------------------------------------------------
# The four gates
# ---------------------------------------------------------------------------

class TestGates:
    def test_audit_ref_zero_is_refused(self):
        """Log before act, enforced on the board that would do the acting."""
        out = FakeStdout()
        fw._handle(_decoded(audit_ref=0), FakeAlvik(), out, False)   # noqa: SLF001
        assert _reply(out).reason == RejectReason.AUDIT_REF_ZERO

    def test_the_local_kill_input_refuses(self):
        out = FakeStdout()
        fw._handle(_decoded(), FakeAlvik(), out, True)               # noqa: SLF001
        assert _reply(out).reason == RejectReason.KILL_SWITCH_ACTIVE

    def test_confidence_below_the_gate_is_refused(self):
        out = FakeStdout()
        fw._handle(_decoded(confidence=0.10), FakeAlvik(), out, False)  # noqa: SLF001
        assert _reply(out).reason == RejectReason.CONFIDENCE_BELOW_THRESHOLD

    def test_an_unknown_action_is_refused(self):
        out = FakeStdout()
        msg = _decoded()
        msg.action_type = 0x7E          # not in MOTOR_MAP
        fw._handle(msg, FakeAlvik(), out, False)                     # noqa: SLF001
        assert _reply(out).reason == RejectReason.UNKNOWN_ACTION

    def test_the_arm_joint_actions_are_refused_on_a_wheeled_robot(self):
        """MOVE_JOINT_* is reserved in the protocol for hardware this rig does
        not have. The Alvik answers UNKNOWN_ACTION rather than guessing."""
        out = FakeStdout()
        fw._handle(_decoded(action_type=ActionType.MOVE_JOINT_1),     # noqa: SLF001
                   FakeAlvik(), out, False)
        assert _reply(out).reason == RejectReason.UNKNOWN_ACTION

    def test_audit_ref_is_checked_before_the_kill_input(self):
        """Priority order matters: the audit gate is unconditional and first,
        so no system state can mask a command that was never logged."""
        out = FakeStdout()
        fw._handle(_decoded(audit_ref=0), FakeAlvik(), out, True)     # noqa: SLF001
        assert _reply(out).reason == RejectReason.AUDIT_REF_ZERO

    def test_a_lawful_command_executes_and_is_acknowledged(self):
        out = FakeStdout()
        alvik = FakeAlvik()
        fw._handle(_decoded(), alvik, out, False)                     # noqa: SLF001
        assert alvik.calls
        assert _reply(out).audit_ref == 1


# ---------------------------------------------------------------------------
# The defect the audit found
# ---------------------------------------------------------------------------

class TestFailedMotorCall:
    """`stm32_ack = 1` means accepted *and executed*. It must not be sent for
    a command that raised."""

    def test_a_motor_fault_is_rejected_not_acknowledged(self):
        out = FakeStdout()
        fw._handle(_decoded(), FakeAlvik(raises=True), out, False)     # noqa: SLF001
        reply = _reply(out)
        assert reply.reason == RejectReason.SYSTEM_FAULT

    def test_the_audit_reference_survives_the_fault(self):
        """The row already exists; the reply has to name it, or the governance
        tier cannot mark the right event."""
        out = FakeStdout()
        fw._handle(_decoded(audit_ref=4242), FakeAlvik(raises=True), out, False)  # noqa: SLF001
        assert _reply(out).audit_ref == 4242

    def test_a_motor_fault_does_not_take_the_loop_down(self):
        """Caught rather than propagated: a driver fault must not stop the
        board answering the next command."""
        alvik = FakeAlvik(raises=True)
        out = FakeStdout()
        fw._handle(_decoded(), alvik, out, False)                      # noqa: SLF001
        alvik.raises = False
        out2 = FakeStdout()
        fw._handle(_decoded(audit_ref=2), alvik, out2, False)          # noqa: SLF001
        assert _reply(out2).audit_ref == 2

    def test_exactly_one_reply_per_command_even_on_fault(self):
        """An ACK following the reject would put both answers on the wire."""
        out = FakeStdout()
        fw._handle(_decoded(), FakeAlvik(raises=True), out, False)     # noqa: SLF001
        parser = FrameParser()
        parser.feed(out.written)
        assert len(parser.pop_messages()) == 1


# ---------------------------------------------------------------------------
# The pin that is not a governance control
# ---------------------------------------------------------------------------

class TestKillPinIsNotGovernance:
    def test_the_module_says_so_in_its_own_docstring(self):
        """Guard against the old framing coming back. The relay in the motor
        supply is the control; this pin is a bench convenience."""
        source = (pathlib.Path(_FIRMWARE_DIR) / "main.py").read_text()
        assert "a governance control" in source
        assert "local test input" in source

    def test_the_pin_is_still_available_for_bench_work(self):
        assert fw.KILL_PIN == 4
