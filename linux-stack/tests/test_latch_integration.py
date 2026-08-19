"""
The latch relay as the arbiter and the governance tier see it.

`test_latch.py` covers the driver in isolation. These tests cover the parts
that only appear once the relay is wired into the oversight node: who is
allowed to move it, what happens when it does not move, and what each side is
permitted to believe about it.

The recurring question is the same one the two-source read-back exists to
answer: the difference between a system that intends the motors isolated and
a system that has observed them isolated.
"""

import time

import pytest

from ipc.codec import (
    FrameParser,
    LatchPosition,
    LatchReport,
    LatchRequest,
    OverrideAssert,
    OverrideReason,
    SupervisorHeartbeat,
    SystemState,
    decode,
    encode,
)
from oversight.latch import LatchRelay, LatchState, SimulatedLatch
from oversight.mock_supervisor import ANNUNCIATOR_LATCH_FAULT, MockR4Supervisor
from oversight.supervisor_link import SupervisorLink

SETTLE_S = 0.2


@pytest.fixture
def rig():
    """An arbiter with a fast-polling latch, and a link to it."""
    sim = SimulatedLatch()
    node = MockR4Supervisor(
        heartbeat_timeout_ms=10_000.0,
        latch=LatchRelay(sim, poll_interval_ms=0.0),
    ).start()
    link = SupervisorLink(
        open(node.device, "rb+", buffering=0), heartbeat_interval_s=0.0
    )
    try:
        yield node, link, sim
    finally:
        link.close()
        node.stop()


def _settle(link, seconds: float = SETTLE_S) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        link.poll()
        time.sleep(0.02)


# ---------------------------------------------------------------------------
# Boot and release
# ---------------------------------------------------------------------------

class TestBootAndRelease:
    def test_boots_with_the_motors_isolated(self, rig):
        """Bistable means the contact comes up wherever it was left, so the
        node opens it before doing anything else rather than assuming."""
        node, _, sim = rig
        assert sim.contact is LatchState.OPEN
        assert node.motor_power_cut is True

    def test_first_heartbeat_releases_the_contact(self, rig):
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        assert sim.contact is LatchState.CLOSED
        assert node.motor_power_cut is False

    def test_the_release_is_reported_not_assumed(self, rig):
        node, link, _ = rig
        link.heartbeat(force=True)
        _settle(link)
        assert link.last_latch is not None
        assert link.last_latch.observed is LatchPosition.CLOSED
        assert link.motors_isolated is False

    def test_the_link_believes_nothing_before_a_report(self, rig):
        _, link, _ = rig
        assert link.last_latch is None
        assert link.motors_isolated is False

    def test_an_override_at_boot_keeps_the_contact_open(self, rig):
        node, link, sim = rig
        node.press_button()
        link.heartbeat(force=True)
        _settle(link)
        assert sim.contact is LatchState.OPEN
        assert node.motor_power_cut is True


# ---------------------------------------------------------------------------
# Who may move the relay
# ---------------------------------------------------------------------------

class TestOwnership:
    def test_an_override_opens_the_contact(self, rig):
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        node.press_button()
        _settle(link)
        assert sim.contact is LatchState.OPEN
        assert link.motors_isolated is True

    def test_clearing_closes_it_again(self, rig):
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        node.press_button()
        node.release_button()
        _settle(link)
        assert node.clear_override() is True
        _settle(link)
        assert sim.contact is LatchState.CLOSED

    def test_the_governance_tier_may_request_open(self, rig):
        """Asking for its own decision to be enforced physically. Always
        honoured: more ways to stop are safe."""
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        link.request_latch(LatchPosition.OPEN, audit_ref=42)
        _settle(link)
        assert sim.contact is LatchState.OPEN
        assert node.stats.latch_requests == 1
        assert node.stats.latch_requests_refused == 0

    def test_a_request_to_close_is_refused_while_latched(self, rig):
        """The reason the relay is owned by the arbiter and not by the board
        asking. Nothing on the wire can talk the override down."""
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        node.press_button()
        _settle(link)
        link.request_latch(LatchPosition.CLOSED)
        _settle(link)
        assert sim.contact is LatchState.OPEN
        assert node.stats.latch_requests_refused == 1

    def test_a_request_to_close_is_honoured_when_clear(self, rig):
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        link.request_latch(LatchPosition.OPEN)
        _settle(link)
        link.request_latch(LatchPosition.CLOSED)
        _settle(link)
        assert sim.contact is LatchState.CLOSED
        assert node.stats.latch_requests_refused == 0

    def test_every_request_draws_a_report(self, rig):
        node, link, _ = rig
        link.heartbeat(force=True)
        _settle(link)
        before = link.last_latch
        link.request_latch(LatchPosition.OPEN)
        _settle(link)
        assert link.last_latch is not before


# ---------------------------------------------------------------------------
# When the relay does not do as it is told
# ---------------------------------------------------------------------------

class TestMismatch:
    def test_a_stuck_contact_latches_an_override(self, rig):
        """The relay reports success, the sense line says otherwise, and the
        node stops the rig rather than believing the module."""
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        sim.inject_stuck_contact()
        link.request_latch(LatchPosition.OPEN)
        _settle(link, 0.4)
        assert node.override_active is True
        assert node.override_reason is OverrideReason.LATCH_MISMATCH

    def test_the_annunciator_shows_the_latch_fault(self, rig):
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        sim.inject_stuck_contact()
        link.request_latch(LatchPosition.OPEN)
        _settle(link, 0.4)
        assert node.annunciator == ANNUNCIATOR_LATCH_FAULT

    def test_the_link_raises_its_own_override_on_the_report(self, rig):
        """Both sides act on the same reading. Neither is allowed to rely on
        the other having noticed."""
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        sim.inject_stuck_contact()
        link.request_latch(LatchPosition.OPEN)
        _settle(link, 0.4)
        assert link.override_active is True
        assert link.override_reason is OverrideReason.LATCH_MISMATCH

    def test_a_mismatch_is_counted(self, rig):
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        sim.inject_stuck_contact()
        link.request_latch(LatchPosition.OPEN)
        _settle(link, 0.4)
        assert node.stats.latch_mismatches >= 1

    def test_a_contact_that_drifts_is_caught_by_polling(self, rig):
        """No edge, no interrupt. Only a poll finds this."""
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        node.press_button()
        _settle(link)
        sim._contact = LatchState.CLOSED   # noqa: SLF001 - a weld letting go
        _settle(link, 0.4)
        assert node.stats.latch_mismatches >= 1

    def test_motor_power_cut_is_false_while_the_contact_is_stuck(self, rig):
        """The dangerous direction: intent must never be read as isolation."""
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        sim.inject_stuck_contact()
        node.press_button()
        _settle(link)
        assert node.halt_intended is True
        assert node.motor_power_cut is False

    def test_the_link_will_not_claim_isolation_it_has_not_seen(self, rig):
        node, link, sim = rig
        link.heartbeat(force=True)
        _settle(link)
        sim.inject_stuck_contact()
        node.press_button()
        _settle(link)
        assert link.motors_isolated is False


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------

class TestWireFormat:
    def test_report_round_trips(self):
        msg = LatchReport(
            commanded=LatchPosition.OPEN, reported=LatchPosition.CLOSED,
            observed=LatchPosition.UNKNOWN, transitions=7, mismatches=2,
        )
        assert decode(encode(msg)) == msg

    def test_request_round_trips(self):
        msg = LatchRequest(audit_ref=2**40, desired=LatchPosition.CLOSED)
        assert decode(encode(msg)) == msg

    @pytest.mark.parametrize("position", list(LatchPosition))
    def test_every_position_round_trips(self, position):
        msg = LatchRequest(audit_ref=1, desired=position)
        assert decode(encode(msg)).desired is position

    def test_report_agrees_matches_the_driver(self):
        assert LatchReport(
            LatchPosition.OPEN, LatchPosition.OPEN, LatchPosition.OPEN, 0, 0
        ).agrees
        assert not LatchReport(
            LatchPosition.OPEN, LatchPosition.OPEN, LatchPosition.UNKNOWN, 0, 0
        ).agrees

    def test_wire_sizes(self):
        assert len(encode(LatchRequest(1, LatchPosition.OPEN))) == 4 + 9 + 2
        assert len(encode(LatchReport(
            LatchPosition.OPEN, LatchPosition.OPEN, LatchPosition.OPEN, 0, 0
        ))) == 4 + 11 + 2

    def test_reports_survive_a_mixed_stream(self):
        msgs = [
            SupervisorHeartbeat(1, SystemState.ARMED, 1, 0),
            LatchRequest(1, LatchPosition.OPEN),
            LatchReport(
                LatchPosition.OPEN, LatchPosition.OPEN, LatchPosition.OPEN, 1, 0
            ),
            OverrideAssert(1, OverrideReason.LATCH_MISMATCH),
        ]
        parser = FrameParser()
        parser.feed(b"".join(encode(m) for m in msgs))
        assert parser.pop_messages() == msgs


# ---------------------------------------------------------------------------
# Edges the happy path does not reach
# ---------------------------------------------------------------------------

class TestEdges:
    def test_the_node_exposes_its_relay(self, rig):
        """Accessible for inspection, not for control: moving it still goes
        through the arbiter's own decision path."""
        node, _, _ = rig
        assert isinstance(node.latch, LatchRelay)
        assert node.latch.last_reading is not None

    def test_an_uncommanded_reading_raises_no_override(self):
        """Before the arbiter has commanded anything, commanded is UNKNOWN and
        nothing disagrees with it. A fresh node must not veto itself."""
        sim = SimulatedLatch()
        latch = LatchRelay(sim, poll_interval_ms=0.0)
        node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0, latch=latch)
        node._poll_latch()   # noqa: SLF001 - before start(), nothing commanded
        assert node.override_active is False
        assert node.stats.latch_mismatches == 0

    def test_a_report_is_produced_before_any_poll(self):
        """The frame builder polls on demand rather than reporting nothing."""
        sim = SimulatedLatch()
        node = MockR4Supervisor(
            heartbeat_timeout_ms=10_000.0,
            latch=LatchRelay(sim, poll_interval_ms=10_000.0),
        )
        frame = node._latch_report_frame()   # noqa: SLF001
        report = decode(frame)
        assert isinstance(report, LatchReport)
        assert report.observed is LatchPosition.OPEN
