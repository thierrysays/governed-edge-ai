/*
 * parity_harness.cpp - host driver for the R4 oversight firmware logic.
 *
 * The sketch itself cannot run in CI: no Arduino toolchain, no board. But
 * everything that decides behaviour lives in ipc_frame.cpp and
 * supervisor_state.cpp, which are plain C++ with no Arduino headers. This
 * harness compiles those two files for the host and exposes them over a
 * line protocol on stdin, so the Python test suite can drive the real
 * firmware logic and check it frame for frame against the codec and the
 * MockR4Supervisor reference model.
 *
 * Build:
 *   g++ -std=c++17 -Wall -Wextra -I.. -o parity_harness \
 *       parity_harness.cpp ../ipc_frame.cpp ../supervisor_state.cpp
 *
 * Commands, one per line:
 *   TICK <ms>              set the millisecond clock
 *   FEED <hex>             feed bytes into the frame parser
 *   BUTTON <0|1>           set the debounced override button level
 *   CLEAR                  attempt to release the override
 *   REMOTE                 assert an override from the Wi-Fi console
 *   ENC_ASSERT <ts> <r>    print an encoded OVERRIDE_ASSERT frame as hex
 *   ENC_CLEAR <ts>         print an encoded OVERRIDE_CLEAR frame as hex
 *   ENC_ACK <ref> <v>      print an encoded ATTEST_ACK frame as hex
 *   CRC <ascii>            print the CRC-16/CCITT of the ASCII argument
 *   STATE                  print the current state line
 *   RETAINED               print every retained digest, oldest first
 *   LATCH_HALT             open the contact (cut the motor supply)
 *   LATCH_PERMIT           close the contact
 *   LATCH_POLL             read both sources now
 *   LATCH_DUE              poll only if the cadence has elapsed
 *   LATCH_STATE            print the latch line
 *   LATCH_STICK <0|1>      weld the contact, or release it
 *   LATCH_SENSE <0|1>      break the sense line, or repair it
 *   LATCH_POWERCYCLE       remove and restore power to the module
 *   QUIT
 *
 * Governed Edge AI - Glossolalie Advisory. Apache 2.0.
 */

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "ipc_frame.h"
#include "latch.h"
#include "supervisor_state.h"

static SupervisorState state;
static IpcParser parser;
static uint32_t now_ms = 0;

/* ------------------------------------------------------------------ */
/* Simulated relay, mirroring SimulatedLatch in the Python model.      */
/* Pessimistic by default: the module's register echoes the command    */
/* rather than observing the contact.                                  */
/* ------------------------------------------------------------------ */

struct SimLatch {
  LatchPosition contact = LATCH_OPEN;
  LatchPosition reg = LATCH_OPEN;
  bool echoes = true;
  bool stuck = false;
  bool sense_failed = false;
  unsigned pulses_open = 0;
  unsigned pulses_close = 0;
};

static SimLatch sim;
static Latch latch;

static void sim_pulse_open(void *ctx) {
  SimLatch *s = static_cast<SimLatch *>(ctx);
  s->pulses_open++;
  if (s->echoes) { s->reg = LATCH_OPEN; }
  if (!s->stuck) { s->contact = LATCH_OPEN; }
  if (!s->echoes) { s->reg = s->contact; }
}

static void sim_pulse_close(void *ctx) {
  SimLatch *s = static_cast<SimLatch *>(ctx);
  s->pulses_close++;
  if (s->echoes) { s->reg = LATCH_CLOSED; }
  if (!s->stuck) { s->contact = LATCH_CLOSED; }
  if (!s->echoes) { s->reg = s->contact; }
}

static LatchPosition sim_read_register(void *ctx) {
  return static_cast<SimLatch *>(ctx)->reg;
}

static LatchPosition sim_read_sense(void *ctx) {
  SimLatch *s = static_cast<SimLatch *>(ctx);
  return s->sense_failed ? LATCH_UNKNOWN : s->contact;
}

static const char *latch_name(LatchPosition p) {
  switch (p) {
    case LATCH_OPEN:   return "OPEN";
    case LATCH_CLOSED: return "CLOSED";
    default:           return "UNKNOWN";
  }
}

static void print_latch() {
  std::cout << "LATCH commanded=" << latch_name(latch.commanded)
            << " reported=" << latch_name(latch.last.reported)
            << " observed=" << latch_name(latch.last.observed)
            << " agrees=" << (latch.has_reading && latch_reading_agrees(&latch.last) ? 1 : 0)
            << " enforcing=" << (latch_enforcing(&latch) ? 1 : 0)
            << " transitions=" << latch.transitions
            << " mismatches=" << latch.mismatches
            << " pulses_open=" << sim.pulses_open
            << " pulses_close=" << sim.pulses_close
            << std::endl;
}

static std::string to_hex(const uint8_t *data, size_t len) {
  static const char *digits = "0123456789abcdef";
  std::string out;
  out.reserve(len * 2);
  for (size_t i = 0; i < len; i++) {
    out.push_back(digits[data[i] >> 4]);
    out.push_back(digits[data[i] & 0x0F]);
  }
  return out;
}

static std::vector<uint8_t> from_hex(const std::string &hex) {
  std::vector<uint8_t> out;
  for (size_t i = 0; i + 1 < hex.size(); i += 2) {
    out.push_back((uint8_t)std::stoul(hex.substr(i, 2), nullptr, 16));
  }
  return out;
}

static const char *annunciator_name(Annunciator a) {
  switch (a) {
    case ANN_OVERRIDE: return "OVERRIDE";
    case ANN_STALE:    return "STALE";
    case ANN_ATTEST:   return "ATTEST";
    default:           return "WATCHING";
  }
}

static void print_state() {
  std::cout << "STATE mode="
            << (state.mode == SUP_OVERRIDE ? "OVERRIDE" : "WATCHING")
            << " reason=" << (unsigned)state.override_reason
            << " annunciator=" << annunciator_name(supervisor_annunciator(&state))
            << " kill_line=" << (supervisor_kill_line(&state) ? 1 : 0)
            << " last_ref=" << (unsigned long long)state.last_ref
            << " retained=" << (unsigned)state.ring_count
            << " heartbeats=" << state.heartbeats_received
            << " digests=" << state.digests_received
            << " asserted=" << state.overrides_asserted
            << " cleared=" << state.overrides_cleared
            << " chain_faults=" << state.chain_faults
            << std::endl;
}

int main() {
  supervisor_init(&state, SUP_HEARTBEAT_TIMEOUT_MS, now_ms);
  ipc_parser_reset(&parser);

  LatchIo io;
  io.pulse_open = sim_pulse_open;
  io.pulse_close = sim_pulse_close;
  io.read_register = sim_read_register;
  io.read_sense = sim_read_sense;
  io.ctx = &sim;
  latch_init(&latch, io);

  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream in(line);
    std::string cmd;
    in >> cmd;

    if (cmd == "QUIT" || cmd.empty()) {
      break;
    }

    if (cmd == "TICK") {
      in >> now_ms;
      if (supervisor_tick(&state, now_ms)) {
        std::cout << "TX OVERRIDE_ASSERT " << (unsigned)OVR_GOVERNANCE_HEARTBEAT_LOST
                  << std::endl;
      }
      std::cout << "OK" << std::endl;

    } else if (cmd == "FEED") {
      std::string hex;
      in >> hex;
      SupervisorHeartbeat hb;
      AttestDigest digest;
      for (uint8_t byte : from_hex(hex)) {
        const uint8_t hit = ipc_parser_feed(&parser, byte, &hb, &digest);
        if (hit == MSG_SUPERVISOR_HEARTBEAT) {
          supervisor_on_heartbeat(&state, &hb, now_ms);
          std::cout << "RX HEARTBEAT ref=" << (unsigned long long)hb.last_audit_ref
                    << " state=" << (unsigned)hb.system_state
                    << " events=" << hb.events_logged
                    << " commands=" << hb.commands_sent << std::endl;
        } else if (hit == MSG_ATTEST_DIGEST) {
          const uint8_t verdict = supervisor_on_digest(&state, &digest);
          uint8_t frame[IPC_MAX_FRAME];
          const size_t n = ipc_encode_attest_ack(frame, digest.audit_ref, verdict);
          std::cout << "RX DIGEST ref=" << (unsigned long long)digest.audit_ref
                    << " verdict=" << (unsigned)verdict
                    << " ack=" << to_hex(frame, n) << std::endl;
        }
      }
      std::cout << "OK" << std::endl;

    } else if (cmd == "BUTTON") {
      int level = 0;
      in >> level;
      if (supervisor_set_button(&state, level != 0)) {
        std::cout << "TX OVERRIDE_ASSERT " << (unsigned)OVR_OPERATOR_BUTTON << std::endl;
      }
      std::cout << "OK" << std::endl;

    } else if (cmd == "CLEAR") {
      std::cout << (supervisor_clear_override(&state, now_ms) ? "CLEARED" : "REFUSED")
                << std::endl;

    } else if (cmd == "REMOTE") {
      if (supervisor_assert_override(&state, OVR_REMOTE_CONSOLE)) {
        std::cout << "TX OVERRIDE_ASSERT " << (unsigned)OVR_REMOTE_CONSOLE << std::endl;
      }
      std::cout << "OK" << std::endl;

    } else if (cmd == "ENC_ASSERT") {
      unsigned long long ts = 0;
      unsigned reason = 0;
      in >> ts >> reason;
      uint8_t frame[IPC_MAX_FRAME];
      const size_t n = ipc_encode_override_assert(frame, ts, (uint8_t)reason);
      std::cout << "HEX " << to_hex(frame, n) << std::endl;

    } else if (cmd == "ENC_CLEAR") {
      unsigned long long ts = 0;
      in >> ts;
      uint8_t frame[IPC_MAX_FRAME];
      const size_t n = ipc_encode_override_clear(frame, ts);
      std::cout << "HEX " << to_hex(frame, n) << std::endl;

    } else if (cmd == "ENC_ACK") {
      unsigned long long ref = 0;
      unsigned verdict = 0;
      in >> ref >> verdict;
      uint8_t frame[IPC_MAX_FRAME];
      const size_t n = ipc_encode_attest_ack(frame, ref, (uint8_t)verdict);
      std::cout << "HEX " << to_hex(frame, n) << std::endl;

    } else if (cmd == "CRC") {
      std::string arg;
      in >> arg;
      std::cout << "CRC " << ipc_crc16_ccitt((const uint8_t *)arg.data(), arg.size())
                << std::endl;

    } else if (cmd == "STATE") {
      print_state();

    } else if (cmd == "LATCH_HALT") {
      latch_enforce_halt(&latch, now_ms);
      print_latch();

    } else if (cmd == "LATCH_PERMIT") {
      latch_permit(&latch, now_ms);
      print_latch();

    } else if (cmd == "LATCH_POLL") {
      latch_poll(&latch, now_ms);
      print_latch();

    } else if (cmd == "LATCH_DUE") {
      LatchReading r;
      std::cout << (latch_poll_if_due(&latch, now_ms, &r) ? "POLLED" : "SKIPPED")
                << std::endl;

    } else if (cmd == "LATCH_STATE") {
      print_latch();

    } else if (cmd == "LATCH_STICK") {
      int on = 0;
      in >> on;
      sim.stuck = (on != 0);
      std::cout << "OK" << std::endl;

    } else if (cmd == "LATCH_SENSE") {
      int broken = 0;
      in >> broken;
      sim.sense_failed = (broken != 0);
      std::cout << "OK" << std::endl;

    } else if (cmd == "LATCH_POWERCYCLE") {
      /* The contact does not move: that is what bistable means. The module's
       * MCU reboots and comes back reflecting the real contact. */
      sim.reg = sim.contact;
      std::cout << "OK" << std::endl;

    } else if (cmd == "RETAINED") {
      uint64_t ref = 0;
      uint8_t digest[IPC_DIGEST_BYTES];
      for (uint8_t i = 0; supervisor_retained(&state, i, &ref, digest); i++) {
        std::cout << "DIGEST " << (unsigned long long)ref << " "
                  << to_hex(digest, IPC_DIGEST_BYTES) << std::endl;
      }
      std::cout << "OK" << std::endl;

    } else {
      std::cout << "ERR unknown command" << std::endl;
    }

    std::cout.flush();
  }
  return 0;
}
