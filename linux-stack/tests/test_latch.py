"""
Tests for the Modulino Latch Relay driver and its simulator.

The GPIO line this replaces failed open on power loss, and no test in the
suite could have caught it: the mocks modelled a state machine and not an
electrical system, so there was no power to lose. `TestBistability` is that
missing test, and it is the reason this module models a contact rather than
a boolean.

The other theme here is that the relay has two sources of truth on purpose.
`reported` comes from the module's own MCU and may only echo the last command;
`observed` comes from a sense line across the contact. Most of these tests are
about what happens when they disagree, because that is the case the design
exists to detect and the one a single-source read-back would miss.
"""

import pytest

from oversight.latch import (
    POLL_INTERVAL_MS_DEFAULT,
    PULSE_MS_DEFAULT,
    LatchReading,
    LatchRelay,
    LatchState,
    SimulatedLatch,
)


@pytest.fixture
def sim():
    return SimulatedLatch()


@pytest.fixture
def latch(sim):
    return LatchRelay(sim, poll_interval_ms=0.0)


# ---------------------------------------------------------------------------
# Starting position
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_contact_starts_open(self, sim):
        """Open is the safe state: a normally-open contact in series with the
        motor supply means no power until something closes it."""
        assert sim.contact is LatchState.OPEN

    def test_commanded_starts_unknown(self, latch):
        """The arbiter has commanded nothing yet, and does not pretend it has."""
        assert latch.commanded is LatchState.UNKNOWN

    def test_not_enforcing_before_the_first_poll(self, latch):
        """Nothing is claimed about a contact that has not been looked at."""
        assert latch.enforcing is False
        assert latch.last_reading is None

    def test_first_poll_finds_the_contact(self, latch):
        reading = latch.poll()
        assert reading.observed is LatchState.OPEN
        assert reading.commanded is LatchState.UNKNOWN
        assert latch.enforcing is True

    def test_unknown_initial_state_is_refused(self):
        with pytest.raises(ValueError, match="open or closed"):
            SimulatedLatch(initial=LatchState.UNKNOWN)


# ---------------------------------------------------------------------------
# Commanding
# ---------------------------------------------------------------------------

class TestCommands:
    def test_enforce_halt_opens_the_contact(self, latch, sim):
        latch.permit()
        reading = latch.enforce_halt()
        assert sim.contact is LatchState.OPEN
        assert reading.observed is LatchState.OPEN
        assert reading.agrees

    def test_permit_closes_the_contact(self, latch, sim):
        reading = latch.permit()
        assert sim.contact is LatchState.CLOSED
        assert reading.observed is LatchState.CLOSED
        assert reading.agrees

    def test_pulse_width_is_the_specified_50_ms(self, latch, sim):
        latch.enforce_halt()
        assert sim.last_pulse_ms == PULSE_MS_DEFAULT

    def test_commands_are_idempotent(self, latch, sim):
        latch.enforce_halt()
        latch.enforce_halt()
        latch.enforce_halt()
        assert sim.contact is LatchState.OPEN
        assert latch.transitions == 1

    def test_transitions_count_changes_not_calls(self, latch):
        latch.enforce_halt()
        latch.permit()
        latch.permit()
        latch.enforce_halt()
        assert latch.transitions == 3

    def test_every_command_reads_back(self, latch, sim):
        """A command whose effect is never checked is the assertion this
        class exists to replace."""
        before = sim.pulses_open
        reading = latch.enforce_halt()
        assert sim.pulses_open == before + 1
        assert reading is latch.last_reading


# ---------------------------------------------------------------------------
# Bistability: the property the GPIO line did not have
# ---------------------------------------------------------------------------

class TestBistability:
    def test_open_contact_survives_a_power_cycle(self, latch, sim):
        """The regression that motivated the whole redesign.

        The GPIO kill line released when its board lost power, so a power cut
        at the oversight node un-isolated the motors. A bistable contact holds
        its position with no coil current at all.
        """
        latch.enforce_halt()
        assert sim.contact is LatchState.OPEN
        sim.power_cycle()
        assert sim.contact is LatchState.OPEN
        assert latch.poll().observed is LatchState.OPEN

    def test_closed_contact_also_survives(self, latch, sim):
        """Bistable means bistable in both directions, not fail-safe by luck."""
        latch.permit()
        sim.power_cycle()
        assert sim.contact is LatchState.CLOSED

    def test_the_module_register_forgets_across_a_power_cycle(self, latch, sim):
        """The MCU reboots and loses any memory of the last command, so its
        register comes back reflecting the real contact."""
        latch.enforce_halt()
        sim.inject_stuck_contact()
        latch.permit()                       # register says CLOSED, contact OPEN
        assert sim.read_register() is LatchState.CLOSED
        sim.power_cycle()
        assert sim.read_register() is LatchState.OPEN

    def test_arbiter_does_not_assume_position_after_a_restart(self, sim):
        """A new arbiter against an existing relay starts from UNKNOWN and
        finds out, rather than assuming the contact is where it left it."""
        first = LatchRelay(sim, poll_interval_ms=0.0)
        first.enforce_halt()
        sim.power_cycle()
        second = LatchRelay(sim, poll_interval_ms=0.0)
        assert second.commanded is LatchState.UNKNOWN
        assert second.poll().observed is LatchState.OPEN


# ---------------------------------------------------------------------------
# Two sources, and what happens when they disagree
# ---------------------------------------------------------------------------

class TestSourceDisagreement:
    def test_stuck_contact_is_caught_by_the_sense_line(self, latch, sim):
        """The module reports success and the contact never moved. A
        single-source read-back off the register would report all clear."""
        latch.permit()
        sim.inject_stuck_contact()
        reading = latch.enforce_halt()
        assert reading.reported is LatchState.OPEN      # the module says so
        assert reading.observed is LatchState.CLOSED    # the wire says otherwise
        assert reading.agrees is False
        assert latch.mismatch is True

    def test_a_stuck_contact_does_not_read_as_enforcing(self, latch, sim):
        """The dangerous direction: believing the motors are isolated when
        they are not."""
        latch.permit()
        sim.inject_stuck_contact()
        latch.enforce_halt()
        assert latch.enforcing is False

    def test_mismatches_are_counted(self, latch, sim):
        latch.permit()
        sim.inject_stuck_contact()
        latch.enforce_halt()
        latch.poll()
        latch.poll()
        assert latch.mismatches == 3

    def test_clearing_the_fault_restores_agreement(self, latch, sim):
        latch.permit()
        sim.inject_stuck_contact()
        latch.enforce_halt()
        sim.release_stuck_contact()
        assert latch.enforce_halt().agrees is True

    def test_an_honest_module_agrees_with_the_sense_line(self):
        """With register_echoes_command=False the module reports the contact.
        The design does not depend on which it is, which is the point."""
        sim = SimulatedLatch(register_echoes_command=False)
        latch = LatchRelay(sim, poll_interval_ms=0.0)
        latch.permit()                     # contact closed
        sim.inject_stuck_contact()
        reading = latch.enforce_halt()     # asked to open; contact cannot move
        assert reading.reported == reading.observed is LatchState.CLOSED
        assert reading.commanded is LatchState.OPEN
        assert reading.agrees is False     # still caught, by commanded alone


# ---------------------------------------------------------------------------
# A sense line that cannot be read
# ---------------------------------------------------------------------------

class TestSenseFailure:
    def test_unreadable_sense_gives_unknown(self, latch, sim):
        sim.inject_sense_failure()
        assert latch.poll().observed is LatchState.UNKNOWN

    def test_unknown_never_counts_as_enforcing(self, latch, sim):
        """An unreadable sense line is not evidence that the motors are
        isolated, and must never be rounded up to one."""
        latch.enforce_halt()
        assert latch.enforcing is True
        sim.inject_sense_failure()
        latch.poll()
        assert latch.enforcing is False

    def test_unknown_never_agrees(self, latch, sim):
        latch.enforce_halt()
        sim.inject_sense_failure()
        assert latch.poll().agrees is False

    def test_repair_restores_the_reading(self, latch, sim):
        latch.enforce_halt()
        sim.inject_sense_failure()
        latch.poll()
        sim.repair_sense()
        assert latch.poll().agrees is True


# ---------------------------------------------------------------------------
# Polling cadence
# ---------------------------------------------------------------------------

class TestPolling:
    def test_poll_if_due_respects_the_interval(self, sim):
        latch = LatchRelay(sim, poll_interval_ms=10_000.0)
        assert latch.poll_if_due() is not None    # first call is always due
        assert latch.poll_if_due() is None

    def test_poll_if_due_fires_once_the_interval_elapses(self, sim):
        latch = LatchRelay(sim, poll_interval_ms=0.0)
        assert latch.poll_if_due() is not None
        assert latch.poll_if_due() is not None

    def test_default_cadence_is_documented(self):
        assert POLL_INTERVAL_MS_DEFAULT == 100.0

    def test_polling_detects_a_contact_that_moved_on_its_own(self, latch, sim):
        """Polling rather than waiting for an edge is what catches this: a
        contact that drifts raises no interrupt, so an edge-driven read-back
        would never fire."""
        latch.enforce_halt()
        assert latch.mismatch is False
        sim._contact = LatchState.CLOSED   # noqa: SLF001 - a weld letting go
        assert latch.poll().agrees is False


# ---------------------------------------------------------------------------
# LatchReading semantics
# ---------------------------------------------------------------------------

class TestReading:
    def test_agrees_needs_all_three(self):
        assert LatchReading(LatchState.OPEN, LatchState.OPEN, LatchState.OPEN).agrees
        assert not LatchReading(
            LatchState.OPEN, LatchState.CLOSED, LatchState.OPEN
        ).agrees
        assert not LatchReading(
            LatchState.CLOSED, LatchState.OPEN, LatchState.OPEN
        ).agrees

    def test_enforcing_is_about_the_observation_alone(self):
        """Not about what was commanded. Intent is not isolation."""
        assert LatchReading(
            LatchState.CLOSED, LatchState.CLOSED, LatchState.OPEN
        ).enforcing
        assert not LatchReading(
            LatchState.OPEN, LatchState.OPEN, LatchState.UNKNOWN
        ).enforcing

    def test_describe_names_all_three_sources(self):
        text = LatchReading(
            LatchState.OPEN, LatchState.CLOSED, LatchState.UNKNOWN
        ).describe()
        assert "commanded=OPEN" in text
        assert "reported=CLOSED" in text
        assert "observed=UNKNOWN" in text
