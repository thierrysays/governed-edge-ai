"""
Modulino Latch Relay: the physical safety path.

A bistable relay (ABX00138, HFE60/3-1HT-L2) with its normally-open contact in
series with the Alvik's motor supply. The arbiter opens it to enforce a HALT
and closes it to permit motion. Nothing else in the system can reach it.

Why a latch rather than a driven line
-------------------------------------
The arrangement this replaces was a GPIO line from the oversight node into the
Alvik's kill-switch pin, and it had two faults that a bistable relay does not.

It failed **open**. Cut power to the board holding the line and the line
released, which is the wrong direction for a safety control. A bistable relay
holds its contact position with no coil current at all, so its state survives
losing the board, losing Linux, and a reboot of either.

It needed the governed component's cooperation. The kill line worked only
because the Alvik's firmware chose to read that pin; reflash the Alvik and it
meant nothing. This contact is in the motor supply. There is nothing for the
Alvik to agree to.

Two sources of truth, deliberately
----------------------------------
`reported` is what the module's own microcontroller says. `observed` comes from
a sense circuit on the contact itself.

They are kept separate because they answer different questions. The Modulino
line puts a small MCU behind the I2C interface, so a state register on it most
likely echoes the last command it accepted rather than observing where the
contact physically sits. Trusting it would reproduce the error this read-back
exists to eliminate: the component that was told to stop reporting that it
stopped, which is the same shape as `stm32_ack` being believed on its own.

So the sense line is the source of truth and the register is a cross-check,
and a disagreement between them is more informative than either agreeing with
itself. It means a failed relay, a broken sense line, or a module lying about
its own state, and all three are worth an audit row.

Why the sense line is two channels and not one
----------------------------------------------
A single sense input cannot tell "the contact is here" from "I cannot see the
contact". Whichever way that pin is wired, one of its two readings is also
what a broken wire produces, and so one physical position becomes
indistinguishable from a fault. If that position is OPEN, a cut wire reports
the motors isolated when nothing is known about them, which is the one claim
this whole module exists to refuse.

So the observation is **antivalent**: two channels that must disagree with each
other. One is energised only when the contact is open, the other only when the
motor rail is live. Complementary readings give OPEN or CLOSED; any other
combination, including both channels dark after a cut harness or a flat
battery, gives UNKNOWN. Nothing rounds UNKNOWN up to isolation, so a fault in
the observation costs availability and never safety.

`SimulatedLatch` models both channels for this reason: the failure worth
testing is the harness, not the contact.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol

#: Datasheet figure from the diagram: SET and RESET are 50 ms pulses.
PULSE_MS_DEFAULT: float = 50.0

#: How often the arbiter reads the contact back. Section 15.2 of the
#: reconciliation: polled at a fixed cadence rather than interrupt-driven, so
#: a latch that silently failed to move is caught, not just transitions.
POLL_INTERVAL_MS_DEFAULT: float = 100.0


class LatchState(IntEnum):
    """Contact position, named for what it does to the motors.

    The contact is normally open and wired in series with the motor supply,
    so OPEN is the safe state and the power-on state of an unlatched relay.
    """

    OPEN = 0      # contact open, motor supply cut, HALT enforced
    CLOSED = 1    # contact closed, motor supply available
    UNKNOWN = 2   # no reading yet, or the sense line is unreadable


@dataclass(frozen=True)
class LatchReading:
    """One poll of the relay, from both sources."""

    commanded: LatchState   # what the arbiter last asked for
    reported: LatchState    # what the module's MCU says
    observed: LatchState    # what the sense line across the contact says

    @property
    def agrees(self) -> bool:
        """True when all three agree and the observation is usable."""
        return (
            self.observed is not LatchState.UNKNOWN
            and self.commanded == self.observed == self.reported
        )

    @property
    def enforcing(self) -> bool:
        """True when the contact is observed open: motor supply actually cut.

        Deliberately false when the observation is UNKNOWN. An unreadable
        sense line is not evidence that the motors are isolated.
        """
        return self.observed is LatchState.OPEN

    def describe(self) -> str:
        return (
            f"commanded={self.commanded.name} reported={self.reported.name} "
            f"observed={self.observed.name}"
        )


class LatchTransport(Protocol):
    """The wiring, abstracted: two I2C pulses, a register, and a sense pin."""

    def pulse_open(self, pulse_ms: float) -> None:
        """Drive the coil that opens the contact (motor supply cut)."""

    def pulse_close(self, pulse_ms: float) -> None:
        """Drive the coil that closes the contact (motor supply available)."""

    def read_register(self) -> LatchState:
        """What the module's own MCU reports. May echo the command."""

    def read_sense(self) -> LatchState:
        """GPIO across the contact. The source of truth."""


class LatchRelay:
    """Arbiter-side driver for the latch relay.

    Thread-safe: the arbiter's reader thread and its poll timer both touch it.

    Parameters
    ----------
    transport:
        The wiring. `SimulatedLatch` in tests and in `--supervisor mock`.
    pulse_ms:
        Coil pulse width. 50 ms per the module's specification.
    poll_interval_ms:
        Minimum interval between sense-line reads in `poll_if_due()`.
    """

    def __init__(
        self,
        transport: LatchTransport,
        *,
        pulse_ms: float = PULSE_MS_DEFAULT,
        poll_interval_ms: float = POLL_INTERVAL_MS_DEFAULT,
    ) -> None:
        self._transport = transport
        self._pulse_ms = pulse_ms
        self._poll_interval_s = poll_interval_ms / 1000.0
        self._lock = threading.Lock()

        # The arbiter comes up having commanded nothing. It does not assume
        # the contact is where it would like it to be: the first poll finds
        # out, and until then the state is UNKNOWN rather than optimistic.
        self._commanded = LatchState.UNKNOWN
        self._last_reading: LatchReading | None = None
        self._last_poll_at = 0.0
        self._transitions = 0
        self._mismatches = 0

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def enforce_halt(self) -> LatchReading:
        """Open the contact: cut the motor supply. Idempotent."""
        return self._command(LatchState.OPEN)

    def permit(self) -> LatchReading:
        """Close the contact: make the motor supply available. Idempotent."""
        return self._command(LatchState.CLOSED)

    def _command(self, target: LatchState) -> LatchReading:
        with self._lock:
            changed = self._commanded is not target
            self._commanded = target
            if target is LatchState.OPEN:
                self._transport.pulse_open(self._pulse_ms)
            else:
                self._transport.pulse_close(self._pulse_ms)
            if changed:
                self._transitions += 1
        # Read back immediately: a command whose effect is never checked is
        # the assertion this class exists to replace.
        return self.poll()

    # ------------------------------------------------------------------
    # Read-back
    # ------------------------------------------------------------------

    def poll(self) -> LatchReading:
        """Read both sources now and record the result."""
        reported = self._transport.read_register()
        observed = self._transport.read_sense()
        with self._lock:
            reading = LatchReading(
                commanded=self._commanded, reported=reported, observed=observed
            )
            self._last_reading = reading
            self._last_poll_at = time.monotonic()
            if self._commanded is not LatchState.UNKNOWN and not reading.agrees:
                self._mismatches += 1
            return reading

    def poll_if_due(self) -> LatchReading | None:
        """Poll only if the interval has elapsed. Returns None otherwise."""
        with self._lock:
            if (time.monotonic() - self._last_poll_at) < self._poll_interval_s:
                return None
        return self.poll()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def commanded(self) -> LatchState:
        with self._lock:
            return self._commanded

    @property
    def last_reading(self) -> LatchReading | None:
        with self._lock:
            return self._last_reading

    @property
    def enforcing(self) -> bool:
        """True when the last reading observed the contact open.

        False before the first poll. Nothing is assumed about a contact that
        has not been looked at.
        """
        with self._lock:
            return self._last_reading is not None and self._last_reading.enforcing

    @property
    def mismatch(self) -> bool:
        """True when the last reading did not have all three sources agreeing."""
        with self._lock:
            return self._last_reading is not None and not self._last_reading.agrees

    @property
    def transitions(self) -> int:
        """Commanded state changes since construction."""
        with self._lock:
            return self._transitions

    @property
    def mismatches(self) -> int:
        """Polls where the sources disagreed. Each one is worth an audit row."""
        with self._lock:
            return self._mismatches


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class SimulatedLatch:
    """Software model of the ABX00138, pessimistic where the datasheet is unread.

    Three properties are modelled because each of them is load-bearing.

    **Bistable.** `power_cycle()` leaves the contact where it was. The GPIO
    line this replaces failed open on power loss and no test could have caught
    it, because the old mock modelled a state machine and not an electrical
    system. `test_survives_power_cycle` is that missing test.

    **The register may lie.** `register_echoes_command` defaults to True, which
    models the pessimistic reading of section 15.2: the module's MCU reports
    what it was last told rather than what the contact did. With
    `inject_stuck_contact()` the register and the sense line then disagree,
    which is the fault the two-source read-back exists to find.

    **The sense line can fail.** The observation is an antivalent pair, so
    `inject_sense_failure()` breaks one channel and the two stop being
    complementary, which reads as UNKNOWN rather than as a position. UNKNOWN
    must never be treated as evidence that the motors are isolated.
    """

    def __init__(
        self,
        *,
        initial: LatchState = LatchState.OPEN,
        register_echoes_command: bool = True,
    ) -> None:
        if initial is LatchState.UNKNOWN:
            raise ValueError("a relay contact is either open or closed")
        self._contact = initial
        self._register = initial
        self._echoes = register_echoes_command
        self._stuck = False
        # The two sense channels, independently breakable. Channel A is
        # energised only while the contact is open, channel B only while the
        # motor rail is live. A broken channel reads dark.
        self._sense_a_live = True
        self._sense_b_live = True
        self.pulses_open = 0
        self.pulses_close = 0
        self.last_pulse_ms: float | None = None

    # -- LatchTransport ------------------------------------------------

    def pulse_open(self, pulse_ms: float) -> None:
        self.pulses_open += 1
        self.last_pulse_ms = pulse_ms
        self._register = LatchState.OPEN if self._echoes else self._contact
        if not self._stuck:
            self._contact = LatchState.OPEN
        if not self._echoes:
            self._register = self._contact

    def pulse_close(self, pulse_ms: float) -> None:
        self.pulses_close += 1
        self.last_pulse_ms = pulse_ms
        self._register = LatchState.CLOSED if self._echoes else self._contact
        if not self._stuck:
            self._contact = LatchState.CLOSED
        if not self._echoes:
            self._register = self._contact

    def read_register(self) -> LatchState:
        return self._register

    def read_sense(self) -> LatchState:
        """Read the antivalent pair and decode it.

        Complementary channels name a position. Anything else, whether both
        dark from a cut harness or both lit from a shorted one, is UNKNOWN.
        """
        channel_a = self._sense_a_live and self._contact is LatchState.OPEN
        channel_b = self._sense_b_live and self._contact is LatchState.CLOSED
        if channel_a and not channel_b:
            return LatchState.OPEN
        if channel_b and not channel_a:
            return LatchState.CLOSED
        return LatchState.UNKNOWN

    # -- Fault injection and inspection --------------------------------

    @property
    def contact(self) -> LatchState:
        """Physical contact position. Test-only: no wire reports this."""
        return self._contact

    def power_cycle(self) -> None:
        """Remove and restore power.

        The contact does not move: that is what bistable means, and it is the
        property the GPIO line it replaces did not have. The module's MCU does
        reboot, losing any memory of the last command, so its register comes
        back reflecting the real contact position.
        """
        self._register = self._contact

    def inject_stuck_contact(self) -> None:
        """The contact welds or the coil fails. Commands stop having effect."""
        self._stuck = True

    def release_stuck_contact(self) -> None:
        self._stuck = False

    def inject_sense_failure(self, channel: str = "both") -> None:
        """Break a sense channel: "a", "b", or both.

        Breaking either one is enough. The pair stops being complementary and
        the observation becomes UNKNOWN, which is the point of wiring it this
        way: a fault in the observation is visible as a fault rather than as a
        confident reading of the wrong position.
        """
        if channel not in {"a", "b", "both"}:
            raise ValueError("channel must be 'a', 'b' or 'both'")
        if channel in {"a", "both"}:
            self._sense_a_live = False
        if channel in {"b", "both"}:
            self._sense_b_live = False

    def repair_sense(self) -> None:
        self._sense_a_live = True
        self._sense_b_live = True
