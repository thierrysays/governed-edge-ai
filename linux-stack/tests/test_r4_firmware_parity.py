"""
Parity tests: the compiled R4 firmware logic against the Python reference model.

MockR4Supervisor is the specification for the oversight node; the C++ in
r4-supervisor/ is the port that will actually run on the board. Two
implementations of one state machine drift unless something checks them, and
these tests are that check.

The sketch cannot run here, but everything that decides behaviour lives in
r4-supervisor/ipc_frame.cpp and supervisor_state.cpp, which are plain C++
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
_SOURCES = ["test/parity_harness.cpp", "ipc_frame.cpp", "supervisor_state.cpp"]

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
