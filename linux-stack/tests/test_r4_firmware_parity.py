"""
Parity tests: the compiled R4 firmware logic against the Python reference model.

MockR4Supervisor is the specification for the oversight node; the C++ in
r4-supervisor/ is the port that will actually run on the board. Two
implementations of one state machine drift unless something checks them, and
these tests are that check.

The sketch cannot run here, but everything that decides behaviour lives in
r4-supervisor/ipc_frame.cpp, supervisor_state.cpp and latch.cpp, which are plain C++
with no Arduino headers. The harness in r4-supervisor/test/parity_harness.cpp
compiles them for the host and drives them over a line protocol.

Skipped when no C++ compiler is available. That is the honest arrangement:
the parity check is a real gate where a toolchain exists, and its absence is
reported rather than papered over.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ipc.codec import (
    AttestAck,
    AttestDigest,
    AttestVerdict,
    LatchPosition,
    LatchReport,
    LatchRequest,
    MsgType,
    OverrideAssert,
    OverrideClear,
    OverrideReason,
    SupervisorHeartbeat,
    SystemState,
    crc16_ccitt,
    decode,
    encode,
)

_FIRMWARE = Path(__file__).resolve().parents[2] / "r4-supervisor"
_SOURCES = [
    "test/parity_harness.cpp", "ipc_frame.cpp", "supervisor_state.cpp", "latch.cpp",
]

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None, reason="no C++ compiler: firmware parity not checked"
)


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """Compile the firmware logic for the host once per test session."""
    binary = tmp_path_factory.mktemp("r4") / "parity_harness"
    result = subprocess.run(  # noqa: S603
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",  # noqa: S607
         "-I", str(_FIRMWARE), "-o", str(binary),
         *[str(_FIRMWARE / src) for src in _SOURCES]],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"firmware did not compile:\n{result.stderr}")
    return binary


def run(harness, *commands: str) -> list[str]:
    """Drive the harness with a script and return its output lines."""
    script = "\n".join([*commands, "QUIT", ""])
    result = subprocess.run(  # noqa: S603
        [str(harness)], input=script, capture_output=True, text=True,
        timeout=30, check=True,
    )
    return result.stdout.splitlines()


def hexed(msg) -> str:
    return encode(msg).hex()


def state_of(lines: list[str]) -> dict[str, str]:
    """Parse the last STATE line into a dict."""
    for line in reversed(lines):
        if line.startswith("STATE "):
            return dict(field.split("=", 1) for field in line.split()[1:])
    raise AssertionError(f"no STATE line in {lines}")


def _heartbeat(ref: int = 1, events: int = 1, sent: int = 0) -> str:
    return hexed(SupervisorHeartbeat(
        last_audit_ref=ref, system_state=SystemState.ARMED,
        events_logged=events, commands_sent=sent,
    ))


def _digest(ref: int, fill: int = 0xAB) -> str:
    return hexed(AttestDigest(audit_ref=ref, digest=bytes([fill]) * 32))


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------

class TestWireFormat:
    def test_crc_matches_the_python_implementation(self, harness):
        (line,) = [x for x in run(harness, "CRC 123456789") if x.startswith("CRC ")]
        assert int(line.split()[1]) == crc16_ccitt(b"123456789")

    def test_override_assert_decodes_in_python(self, harness):
        lines = run(harness, "ENC_ASSERT 123456789 1")
        frame = bytes.fromhex(lines[0].split()[1])
        assert decode(frame) == OverrideAssert(
            timestamp_us=123_456_789, reason=OverrideReason.OPERATOR_BUTTON
        )

    def test_override_clear_decodes_in_python(self, harness):
        lines = run(harness, "ENC_CLEAR 42")
        assert decode(bytes.fromhex(lines[0].split()[1])) == OverrideClear(42)

    @pytest.mark.parametrize("verdict", list(AttestVerdict))
    def test_attest_ack_decodes_in_python(self, harness, verdict):
        lines = run(harness, f"ENC_ACK 7 {int(verdict)}")
        assert decode(bytes.fromhex(lines[0].split()[1])) == AttestAck(7, verdict)

    def test_encoded_frames_are_byte_identical(self, harness):
        """Not just decodable: the same bytes on the wire."""
        lines = run(harness, "ENC_ASSERT 999 2", "ENC_CLEAR 999", "ENC_ACK 999 1")
        emitted = [x.split()[1] for x in lines if x.startswith("HEX ")]
        assert emitted == [
            hexed(OverrideAssert(999, OverrideReason.GOVERNANCE_HEARTBEAT_LOST)),
            hexed(OverrideClear(999)),
            hexed(AttestAck(999, AttestVerdict.CHAIN_BREAK)),
        ]


# ---------------------------------------------------------------------------
# Frame parsing
# ---------------------------------------------------------------------------

class TestParsing:
    def test_heartbeat_fields_survive_the_round_trip(self, harness):
        lines = run(harness, "TICK 100", f"FEED {_heartbeat(ref=4096, events=77, sent=9)}")
        rx = [x for x in lines if x.startswith("RX HEARTBEAT")]
        assert rx == ["RX HEARTBEAT ref=4096 state=0 events=77 commands=9"]

    def test_digest_bytes_survive_the_round_trip(self, harness):
        lines = run(harness, "TICK 100", f"FEED {_digest(1, fill=0x5A)}", "RETAINED")
        retained = [x for x in lines if x.startswith("DIGEST ")]
        assert retained == ["DIGEST 1 " + ("5a" * 32)]

    def test_garbage_before_a_frame_is_discarded(self, harness):
        lines = run(harness, "TICK 100", f"FEED 000102{_heartbeat()}")
        assert any(x.startswith("RX HEARTBEAT") for x in lines)

    def test_corrupt_crc_is_dropped(self, harness):
        frame = bytearray(bytes.fromhex(_heartbeat()))
        frame[-1] ^= 0xFF
        lines = run(harness, "TICK 100", f"FEED {frame.hex()}", "STATE")
        assert not any(x.startswith("RX HEARTBEAT") for x in lines)
        assert state_of(lines)["heartbeats"] == "0"

    def test_stream_split_across_feeds(self, harness):
        frame = _heartbeat()
        lines = run(harness, "TICK 100", f"FEED {frame[:10]}", f"FEED {frame[10:]}")
        assert any(x.startswith("RX HEARTBEAT") for x in lines)

    def test_oversized_length_header_does_not_overrun(self, harness):
        """A hostile length field must resynchronise, not corrupt memory."""
        bogus = "a530ff00" + "00" * 8
        lines = run(harness, "TICK 100", f"FEED {bogus}", f"FEED {_heartbeat()}", "STATE")
        assert state_of(lines)["heartbeats"] == "1"


# ---------------------------------------------------------------------------
# State machine parity with MockR4Supervisor
# ---------------------------------------------------------------------------

class TestStateMachineParity:
    def test_starts_watching(self, harness):
        st = state_of(run(harness, "TICK 100", "STATE"))
        assert st["mode"] == "WATCHING"
        assert st["annunciator"] == "WATCHING"

    def test_kill_line_held_before_the_first_heartbeat(self, harness):
        """Parity with the model: the board comes up holding the line."""
        st = state_of(run(harness, "TICK 100", "STATE"))
        assert st["kill_line"] == "1"
        assert st["mode"] == "WATCHING"

    def test_kill_line_releases_on_first_contact(self, harness):
        st = state_of(run(harness, "TICK 100", f"FEED {_heartbeat()}", "STATE"))
        assert st["kill_line"] == "0"

    def test_clear_refused_before_any_heartbeat(self, harness):
        lines = run(harness, "TICK 100", "BUTTON 1", "BUTTON 0", "CLEAR")
        assert "REFUSED" in lines

    def test_button_latches_and_transmits(self, harness):
        lines = run(harness, "TICK 100", f"FEED {_heartbeat()}", "BUTTON 1", "STATE")
        assert "TX OVERRIDE_ASSERT 1" in lines
        st = state_of(lines)
        assert st["mode"] == "OVERRIDE"
        assert st["reason"] == str(int(OverrideReason.OPERATOR_BUTTON))
        assert st["annunciator"] == "OVERRIDE"
        assert st["kill_line"] == "1"

    def test_release_does_not_clear_the_latch(self, harness):
        st = state_of(run(
            harness, "TICK 100", f"FEED {_heartbeat()}", "BUTTON 1", "BUTTON 0", "STATE"
        ))
        assert st["mode"] == "OVERRIDE"

    def test_repeated_press_asserts_once(self, harness):
        st = state_of(run(
            harness, "TICK 100", f"FEED {_heartbeat()}",
            "BUTTON 1", "BUTTON 0", "BUTTON 1", "STATE",
        ))
        assert st["asserted"] == "1"

    def test_clear_refused_while_button_held(self, harness):
        lines = run(harness, "TICK 100", f"FEED {_heartbeat()}", "BUTTON 1", "CLEAR")
        assert "REFUSED" in lines

    def test_clear_succeeds_after_release(self, harness):
        lines = run(
            harness, "TICK 100", f"FEED {_heartbeat()}",
            "BUTTON 1", "BUTTON 0", "CLEAR", "STATE",
        )
        assert "CLEARED" in lines
        assert state_of(lines)["mode"] == "WATCHING"

    def test_clear_refused_while_governance_is_silent(self, harness):
        lines = run(harness, "TICK 100", "TICK 9000", "CLEAR")
        assert "REFUSED" in lines

    def test_watchdog_latches_on_silence(self, harness):
        lines = run(harness, "TICK 100", f"FEED {_heartbeat()}", "TICK 9000", "STATE")
        assert "TX OVERRIDE_ASSERT 2" in lines
        st = state_of(lines)
        assert st["mode"] == "OVERRIDE"
        assert st["annunciator"] == "STALE"

    def test_heartbeats_hold_the_watchdog_off(self, harness):
        script = ["TICK 0"]
        for t in range(500, 5000, 500):
            script += [f"FEED {_heartbeat()}", f"TICK {t}"]
        st = state_of(run(harness, *script, "STATE"))
        assert st["mode"] == "WATCHING"

    def test_sequential_digests_are_retained(self, harness):
        lines = run(
            harness, "TICK 100",
            f"FEED {_digest(1)}", f"FEED {_digest(2)}", f"FEED {_digest(3)}",
            "STATE",
        )
        st = state_of(lines)
        assert st["retained"] == "3"
        assert st["last_ref"] == "3"
        assert st["mode"] == "WATCHING"

    def test_gap_raises_an_override(self, harness):
        lines = run(harness, "TICK 100", f"FEED {_digest(1)}", f"FEED {_digest(5)}", "STATE")
        st = state_of(lines)
        assert st["mode"] == "OVERRIDE"
        assert st["annunciator"] == "ATTEST"
        assert st["chain_faults"] == "1"
        assert st["retained"] == "1"

    def test_rollback_raises_chain_break(self, harness):
        lines = run(
            harness, "TICK 100", f"FEED {_digest(1)}", f"FEED {_digest(2)}",
            f"FEED {_digest(2)}",
        )
        acks = [x for x in lines if x.startswith("RX DIGEST")]
        assert acks[-1].split()[3] == f"verdict={int(AttestVerdict.CHAIN_BREAK)}"

    def test_ack_frames_decode_in_python(self, harness):
        lines = run(harness, "TICK 100", f"FEED {_digest(1)}")
        ack_hex = [x for x in lines if x.startswith("RX DIGEST")][0].split("ack=")[1]
        assert decode(bytes.fromhex(ack_hex)) == AttestAck(1, AttestVerdict.CHAIN_OK)

    def test_first_reason_wins(self, harness):
        lines = run(
            harness, "TICK 100", f"FEED {_heartbeat()}", "BUTTON 1", f"FEED {_digest(9)}",
            "STATE",
        )
        st = state_of(lines)
        assert st["reason"] == str(int(OverrideReason.OPERATOR_BUTTON))
        assert st["asserted"] == "1"

    def test_clearing_an_attestation_override_resyncs(self, harness):
        """Otherwise every later digest gaps against a stale expectation."""
        lines = run(
            harness, "TICK 100",
            f"FEED {_digest(1)}",
            f"FEED {_digest(5)}",                      # gap: override
            f"FEED {_heartbeat(ref=5)}",               # governance reports ref 5
            "CLEAR",
            f"FEED {_digest(6)}",                      # must be accepted now
            "STATE",
        )
        assert "CLEARED" in lines
        st = state_of(lines)
        assert st["mode"] == "WATCHING"
        assert st["last_ref"] == "6"

    def test_remote_console_override(self, harness):
        lines = run(harness, "TICK 100", f"FEED {_heartbeat()}", "REMOTE", "STATE")
        assert f"TX OVERRIDE_ASSERT {int(OverrideReason.REMOTE_CONSOLE)}" in lines
        assert state_of(lines)["mode"] == "OVERRIDE"


# ---------------------------------------------------------------------------
# Ring buffer
# ---------------------------------------------------------------------------

class TestRetainedRing:
    def test_ring_holds_up_to_capacity(self, harness):
        script = ["TICK 100"] + [f"FEED {_digest(i)}" for i in range(1, 65)]
        st = state_of(run(harness, *script, "STATE"))
        assert st["retained"] == "64"

    def test_ring_evicts_the_oldest(self, harness):
        script = ["TICK 100"] + [f"FEED {_digest(i, fill=i % 256)}" for i in range(1, 70)]
        lines = run(harness, *script, "RETAINED")
        refs = [int(x.split()[1]) for x in lines if x.startswith("DIGEST ")]
        assert len(refs) == 64
        assert refs[0] == 6      # 1..5 evicted
        assert refs[-1] == 69
        assert refs == sorted(refs)


# ---------------------------------------------------------------------------
# Cross-check against the Python reference model directly
# ---------------------------------------------------------------------------

class TestAgainstReferenceModel:
    @pytest.mark.parametrize(
        "sequence,expected_verdicts",
        [
            ([1, 2, 3], ["0", "0", "0"]),
            ([1, 3], ["0", "2"]),
            ([1, 2, 1], ["0", "0", "1"]),
            ([2], ["2"]),
        ],
    )
    def test_verdict_sequence_matches_the_model(self, harness, sequence,
                                                expected_verdicts):
        """Same digest sequence, same verdicts, in both implementations."""
        import time

        from oversight.mock_supervisor import MockR4Supervisor

        script = ["TICK 100"] + [f"FEED {_digest(r)}" for r in sequence]
        lines = run(harness, *script)
        firmware = [x.split()[3].split("=")[1] for x in lines if x.startswith("RX DIGEST")]
        assert firmware == expected_verdicts

        with MockR4Supervisor(heartbeat_timeout_ms=10_000.0) as node:
            channel = open(node.device, "rb+", buffering=0)
            try:
                for ref in sequence:
                    channel.write(encode(AttestDigest(ref, bytes([0xAB]) * 32)))
                    time.sleep(0.05)
                model_faults = node.stats.chain_faults
            finally:
                channel.close()

        assert model_faults == sum(1 for v in expected_verdicts if v != "0")

    def test_annunciator_names_match_the_model(self, harness):
        from oversight import mock_supervisor as model

        names = {
            "WATCHING": model.ANNUNCIATOR_WATCHING,
            "OVERRIDE": model.ANNUNCIATOR_OVERRIDE,
            "STALE": model.ANNUNCIATOR_STALE,
            "ATTEST": model.ANNUNCIATOR_ATTEST_ALERT,
        }
        for firmware_name, model_name in names.items():
            assert firmware_name == model_name

    def test_digest_capacity_matches_the_model(self):
        from oversight.mock_supervisor import DIGEST_CAPACITY_DEFAULT

        header = (_FIRMWARE / "supervisor_state.h").read_text()
        assert f"#define SUP_DIGEST_CAPACITY {DIGEST_CAPACITY_DEFAULT}" in header

    def test_heartbeat_timeout_matches_the_model(self):
        from oversight.mock_supervisor import HEARTBEAT_TIMEOUT_MS_DEFAULT

        header = (_FIRMWARE / "supervisor_state.h").read_text()
        expected = int(HEARTBEAT_TIMEOUT_MS_DEFAULT)
        assert f"#define SUP_HEARTBEAT_TIMEOUT_MS {expected}u" in header


def test_python_version_is_supported():
    """Guard against a toolchain that predates the syntax used here."""
    assert sys.version_info >= (3, 11)


# ---------------------------------------------------------------------------
# Latch relay: the C++ driver against the Python model
# ---------------------------------------------------------------------------

class TestLatchProtocolParity:
    """The two latch message types, across the wire and through the arbiter.

    These exist because the gap they close was invisible for a release. The
    firmware drove the relay and read it back correctly, and simply had no
    encoder or decoder for LATCH_REQUEST and LATCH_REPORT, so the governance
    tier could neither ask nor be told. The suite stayed green throughout,
    because it compared the messages both sides implemented rather than the
    ones the specification lists.

    A parity harness that only checks what exists cannot find what is missing.
    The two constants tests below are the cheap guard against that recurring.
    """

    def test_both_message_types_exist_in_the_firmware(self):
        header = (_FIRMWARE / "ipc_frame.h").read_text()
        assert f"#define MSG_LATCH_REQUEST        0x{int(MsgType.LATCH_REQUEST):02X}" in header
        assert f"#define MSG_LATCH_REPORT         0x{int(MsgType.LATCH_REPORT):02X}" in header

    def test_the_firmware_implements_every_oversight_type_the_model_has(self):
        """The guard the harness was missing.

        Every message type on the oversight link must be present in the
        firmware codec. Adding one to the model and forgetting the port is
        exactly what happened, and it should fail here rather than on a bench.
        """
        header = (_FIRMWARE / "ipc_frame.h").read_text()
        oversight = {
            MsgType.SUPERVISOR_HEARTBEAT, MsgType.ATTEST_DIGEST,
            MsgType.LATCH_REQUEST, MsgType.OVERRIDE_ASSERT,
            MsgType.OVERRIDE_CLEAR, MsgType.ATTEST_ACK, MsgType.LATCH_REPORT,
        }
        missing = [t.name for t in oversight if f"0x{int(t):02X}" not in header]
        assert not missing, f"firmware codec is missing {missing}"

    @pytest.mark.parametrize(
        ("commanded", "reported", "observed", "transitions", "mismatches"),
        [
            (LatchPosition.OPEN, LatchPosition.OPEN, LatchPosition.OPEN, 0, 0),
            (LatchPosition.CLOSED, LatchPosition.CLOSED, LatchPosition.CLOSED, 7, 2),
            (LatchPosition.OPEN, LatchPosition.CLOSED, LatchPosition.UNKNOWN,
             2**31, 2**32 - 1),
        ],
    )
    def test_report_encoding_is_byte_identical(
        self, harness, commanded, reported, observed, transitions, mismatches
    ):
        lines = run(harness, f"ENC_LATCH_REPORT {int(commanded)} {int(reported)} "
                             f"{int(observed)} {transitions} {mismatches}")
        got = next(ln.split()[1] for ln in lines if ln.startswith("HEX "))
        assert got == hexed(LatchReport(
            commanded=commanded, reported=reported, observed=observed,
            transitions=transitions, mismatches=mismatches,
        ))

    def test_the_firmware_decodes_a_request_the_model_encoded(self, harness):
        lines = run(harness, f"FEED {hexed(LatchRequest(audit_ref=42, desired=LatchPosition.OPEN))}")
        rx = next(ln for ln in lines if ln.startswith("RX LATCH_REQUEST"))
        assert "ref=42" in rx
        assert f"desired={int(LatchPosition.OPEN)}" in rx
        assert "refused=0" in rx

    def test_a_request_to_open_is_honoured(self, harness):
        lines = run(
            harness,
            "LATCH_PERMIT",
            f"FEED {hexed(LatchRequest(audit_ref=1, desired=LatchPosition.OPEN))}",
            "LATCH_STATE",
        )
        assert latch_of(lines)["observed"] == "OPEN"

    def test_a_request_to_close_is_honoured_when_no_override_stands(self, harness):
        lines = run(
            harness,
            f"FEED {hexed(LatchRequest(audit_ref=1, desired=LatchPosition.CLOSED))}",
            "LATCH_STATE",
        )
        assert latch_of(lines)["observed"] == "CLOSED"

    def test_a_request_to_close_is_refused_while_overridden(self, harness):
        """The asymmetry, in the port as well as in the model. Nothing on the
        wire can talk an override down.

        The LATCH_HALT stands in for the sketch's `drive_latch`, which opens
        the contact on every pass while an override is latched. The harness
        drives the state machine directly and does not run that loop.
        """
        lines = run(
            harness,
            "LATCH_PERMIT",
            "BUTTON 1",
            "LATCH_HALT",
            f"FEED {hexed(LatchRequest(audit_ref=1, desired=LatchPosition.CLOSED))}",
            "LATCH_STATE",
        )
        rx = next(ln for ln in lines if ln.startswith("RX LATCH_REQUEST"))
        assert "refused=1" in rx
        assert latch_of(lines)["observed"] == "OPEN"

    def test_a_request_to_open_is_honoured_even_while_overridden(self, harness):
        """More ways to stop are always safe."""
        lines = run(
            harness,
            "BUTTON 1",
            f"FEED {hexed(LatchRequest(audit_ref=1, desired=LatchPosition.OPEN))}",
            "LATCH_STATE",
        )
        rx = next(ln for ln in lines if ln.startswith("RX LATCH_REQUEST"))
        assert "refused=0" in rx
        assert latch_of(lines)["observed"] == "OPEN"

    def test_every_request_draws_a_report_the_model_can_decode(self, harness):
        lines = run(harness, f"FEED {hexed(LatchRequest(audit_ref=9, desired=LatchPosition.OPEN))}")
        rx = next(ln for ln in lines if ln.startswith("RX LATCH_REQUEST"))
        report_hex = rx.split("report=")[1].split()[0]
        report = decode(bytes.fromhex(report_hex))
        assert isinstance(report, LatchReport)
        assert report.observed is LatchPosition.OPEN

    def test_the_report_carries_the_arbiter_counters(self, harness):
        lines = run(
            harness, "LATCH_PERMIT", "LATCH_HALT",
            f"FEED {hexed(LatchRequest(audit_ref=1, desired=LatchPosition.CLOSED))}",
        )
        rx = next(ln for ln in lines if ln.startswith("RX LATCH_REQUEST"))
        report = decode(bytes.fromhex(rx.split("report=")[1].split()[0]))
        assert report.transitions == 3      # permit, halt, permit
        assert report.mismatches == 0


def latch_of(lines: list[str]) -> dict[str, str]:
    """Parse the last LATCH line into a dict."""
    for line in reversed(lines):
        if line.startswith("LATCH "):
            return dict(field.split("=", 1) for field in line.split()[1:])
    raise AssertionError(f"no LATCH line in {lines}")


class TestLatchParity:
    """Both implementations, driven through the same scenarios.

    The Python model in oversight/latch.py carries the tests; this checks the
    C++ that will actually sit between the battery and the motors behaves
    identically, including in the failure cases.
    """

    def _model(self):
        from oversight.latch import LatchRelay, SimulatedLatch

        sim = SimulatedLatch()
        return LatchRelay(sim, poll_interval_ms=0.0), sim

    def test_initial_reading_matches(self, harness):
        from oversight.latch import LatchState

        st = latch_of(run(harness, "LATCH_POLL"))
        model, _ = self._model()
        reading = model.poll()

        assert st["commanded"] == "UNKNOWN" == reading.commanded.name
        assert st["observed"] == LatchState.OPEN.name == reading.observed.name
        assert st["agrees"] == "0"
        assert reading.agrees is False

    def test_enforce_and_permit_match(self, harness):
        st = latch_of(run(harness, "LATCH_PERMIT", "LATCH_HALT"))
        model, sim = self._model()
        model.permit()
        reading = model.enforce_halt()

        assert st["commanded"] == reading.commanded.name == "OPEN"
        assert st["observed"] == reading.observed.name == "OPEN"
        assert st["agrees"] == "1"
        assert reading.agrees is True
        assert int(st["transitions"]) == model.transitions

    def test_stuck_contact_matches(self, harness):
        """The fault a single-source read-back would miss, in both."""
        st = latch_of(run(harness, "LATCH_PERMIT", "LATCH_STICK 1", "LATCH_HALT"))
        model, sim = self._model()
        model.permit()
        sim.inject_stuck_contact()
        reading = model.enforce_halt()

        assert st["reported"] == reading.reported.name == "OPEN"
        assert st["observed"] == reading.observed.name == "CLOSED"
        assert st["agrees"] == "0"
        assert st["enforcing"] == "0"
        assert reading.agrees is False
        assert model.enforcing is False

    def test_sense_failure_matches(self, harness):
        st = latch_of(run(harness, "LATCH_HALT", "LATCH_SENSE 1", "LATCH_POLL"))
        model, sim = self._model()
        model.enforce_halt()
        sim.inject_sense_failure()
        reading = model.poll()

        assert st["observed"] == reading.observed.name == "UNKNOWN"
        assert st["enforcing"] == "0"
        assert model.enforcing is False, "UNKNOWN must never read as isolated"

    def test_bistability_matches(self, harness):
        """The regression that caused the redesign, checked on both sides."""
        st = latch_of(run(harness, "LATCH_HALT", "LATCH_POWERCYCLE", "LATCH_POLL"))
        model, sim = self._model()
        model.enforce_halt()
        sim.power_cycle()
        reading = model.poll()

        assert st["observed"] == reading.observed.name == "OPEN"
        assert st["agrees"] == "1"
        assert reading.agrees is True

    def test_idempotence_matches(self, harness):
        st = latch_of(run(harness, "LATCH_HALT", "LATCH_HALT", "LATCH_HALT"))
        model, _ = self._model()
        for _ in range(3):
            model.enforce_halt()
        assert int(st["transitions"]) == model.transitions == 1
        assert int(st["pulses_open"]) == 3   # pulses are sent, state changes once

    def test_mismatch_counts_match(self, harness):
        st = latch_of(run(
            harness, "LATCH_PERMIT", "LATCH_STICK 1", "LATCH_HALT",
            "LATCH_POLL", "LATCH_POLL",
        ))
        model, sim = self._model()
        model.permit()
        sim.inject_stuck_contact()
        model.enforce_halt()
        model.poll()
        model.poll()
        assert int(st["mismatches"]) == model.mismatches == 3

    def test_recovery_matches(self, harness):
        st = latch_of(run(
            harness, "LATCH_PERMIT", "LATCH_STICK 1", "LATCH_HALT",
            "LATCH_STICK 0", "LATCH_HALT",
        ))
        model, sim = self._model()
        model.permit()
        sim.inject_stuck_contact()
        model.enforce_halt()
        sim.release_stuck_contact()
        reading = model.enforce_halt()
        assert st["agrees"] == "1"
        assert reading.agrees is True

    def test_poll_cadence_is_honoured(self, harness):
        lines = run(harness, "TICK 0", "LATCH_POLL", "LATCH_DUE", "LATCH_DUE")
        assert lines.count("SKIPPED") == 2, "cadence must suppress repeat polls"

    def test_poll_cadence_elapses(self, harness):
        lines = run(harness, "TICK 0", "LATCH_POLL", "TICK 5000", "LATCH_DUE")
        assert "POLLED" in lines

    def test_constants_match_the_model(self):
        from oversight.latch import POLL_INTERVAL_MS_DEFAULT, PULSE_MS_DEFAULT

        header = (_FIRMWARE / "latch.h").read_text()
        assert f"#define LATCH_PULSE_MS {int(PULSE_MS_DEFAULT)}u" in header
        assert (
            f"#define LATCH_POLL_INTERVAL_MS {int(POLL_INTERVAL_MS_DEFAULT)}u" in header
        )

    def test_i2c_address_matches_the_diagram(self):
        header = (_FIRMWARE / "latch.h").read_text()
        assert "#define LATCH_I2C_ADDR 0x2A" in header

    def test_position_encoding_matches_the_codec(self):
        """The C++ enum and the wire enum must agree, or a report decodes wrong."""
        from ipc.codec import LatchPosition

        header = (_FIRMWARE / "latch.h").read_text()
        assert f"LATCH_OPEN = {int(LatchPosition.OPEN)}" in header
        assert f"LATCH_CLOSED = {int(LatchPosition.CLOSED)}" in header
        assert f"LATCH_UNKNOWN = {int(LatchPosition.UNKNOWN)}" in header

    def test_latch_mismatch_reason_matches_the_codec(self):
        from ipc.codec import OverrideReason

        header = (_FIRMWARE / "ipc_frame.h").read_text()
        assert (
            f"#define OVR_LATCH_MISMATCH            0x{int(OverrideReason.LATCH_MISMATCH):02X}"
            in header
        )

    def test_latch_annunciator_exists_in_both(self):
        from oversight import mock_supervisor as model

        header = (_FIRMWARE / "supervisor_state.h").read_text()
        assert "ANN_LATCH" in header
        assert model.ANNUNCIATOR_LATCH_FAULT == "LATCH"

    def test_the_sketch_no_longer_drives_a_line_into_the_alvik(self):
        """The design rule: governance modules do not attach to the governed
        component. Guard against the wiring coming back."""
        sketch = (_FIRMWARE / "r4_supervisor.ino").read_text()
        assert "KILL_LINE_PIN" not in sketch
        assert "LATCH_SENSE_A_PIN" in sketch
        assert "latch_enforce_halt" in sketch

    def test_the_sketch_senses_the_contact_on_two_channels(self):
        """The hardware glue is the one part of the firmware the parity
        harness cannot exercise, because it is `digitalRead`. So it is checked
        as text instead.

        A single sense pin cannot distinguish a contact position from a cut
        wire, and one of the positions it would confuse with a fault is OPEN,
        which reads as "the motors are isolated". The pair must therefore be
        present, both pulled up, and the sketch must be able to answer
        LATCH_UNKNOWN, which the Python model treats as no observation at all.
        """
        sketch = (_FIRMWARE / "r4_supervisor.ino").read_text()
        assert "LATCH_SENSE_A_PIN" in sketch
        assert "LATCH_SENSE_B_PIN" in sketch
        assert sketch.count("INPUT_PULLUP") >= 4          # 2 buttons, 2 sense
        assert "pinMode(LATCH_SENSE_A_PIN, INPUT_PULLUP)" in sketch
        assert "pinMode(LATCH_SENSE_B_PIN, INPUT_PULLUP)" in sketch
        # The fault answer has to be reachable from the sense glue itself.
        body = sketch.split("static LatchPosition latch_io_read_sense")[1]
        glue = body.split("\n}")[0]
        assert "return LATCH_UNKNOWN;" in glue
