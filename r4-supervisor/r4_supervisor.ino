/*
 * r4_supervisor.ino - Arduino UNO R4 WiFi oversight node.
 *
 * Tier 0 of governed-edge-ai. This board sits outside the
 * perception -> governance -> actuation chain. It issues no commands and
 * runs no model. It watches the governance tier and can stop it.
 *
 * Inbound, from the VENTUNO Q over USB-C serial:
 *   SUPERVISOR_HEARTBEAT   liveness and session counters, every 500 ms
 *   ATTEST_DIGEST          the audit hash chain head, one per logged row
 *
 * Outbound, to the VENTUNO Q:
 *   OVERRIDE_ASSERT        stop issuing commands, with a reason
 *   OVERRIDE_CLEAR         override released at this board
 *   ATTEST_ACK             verdict on the last digest
 *
 * Two enforcement paths, and this is the only board wired to both:
 *   1. Soft veto over the serial link. The governance filter stops
 *      transmitting CommandRequest frames.
 *   2. Physical: a bistable latch relay on this board's Qwiic bus, whose
 *      contact sits in series with the Alvik's motor supply. It holds even
 *      if the VENTUNO Q ignores the soft veto, because there is no motor
 *      supply left to ignore it with.
 *
 * This replaces an earlier GPIO line into the Alvik's kill-switch pin. That
 * line failed open when this board lost power, and it worked only because
 * the Alvik's firmware chose to read the pin: a governance module hanging
 * off the governed component. The relay has neither fault. Its contact is
 * bistable, so it holds with no coil current through a power cut and a Linux
 * reboot, and it is in the supply, so the Alvik has nothing to agree to.
 *
 * Wiring
 *   D2     OVERRIDE_BUTTON_PIN  momentary NC button to GND, INPUT_PULLUP.
 *                               Normally closed: a cut wire reads as pressed.
 *   D4     CLEAR_BUTTON_PIN     momentary NO button to GND, INPUT_PULLUP.
 *   D3     LATCH_SENSE_A_PIN    opto across the relay contact: pulled low only
 *                               while the contact is open.
 *   D5     LATCH_SENSE_B_PIN    opto across the motor rail: pulled low only
 *                               while the rail is live. Antivalent with A;
 *                               see latch_io_read_sense below.
 *   Qwiic  Modulino Latch Relay at I2C 0x2A, and the evidence modules.
 *   USB-C                       serial to the VENTUNO Q at 921600 baud.
 *
 * The state machine lives in supervisor_state.cpp, which is a port of
 * linux-stack/oversight/mock_supervisor.py. That Python model is the
 * specification and carries the test suite; keep the two in step.
 *
 * Build: Arduino IDE or arduino-cli, board arduino:renesas_uno:unor4wifi.
 * See README.md in this directory.
 *
 * Governed Edge AI - Glossolalie Advisory. Apache 2.0.
 */

#include <Wire.h>

#include "Arduino_LED_Matrix.h"
#include "ipc_frame.h"
#include "latch.h"
#include "supervisor_state.h"

/* Set to 1 and fill in arduino_secrets.h to expose the read-only status
 * console and the remote override on the local network. Off by default:
 * an oversight node with fewer network surfaces is a better oversight node. */
#define ENABLE_WIFI_CONSOLE 0

#if ENABLE_WIFI_CONSOLE
#include "WiFiS3.h"
#include "arduino_secrets.h"
static WiFiServer console(8021);
#endif

static const uint8_t OVERRIDE_BUTTON_PIN = 2;
static const uint8_t LATCH_SENSE_A_PIN   = 3;  /* low while the contact is open */
static const uint8_t LATCH_SENSE_B_PIN   = 5;  /* low while the motor rail is live */
static const uint8_t CLEAR_BUTTON_PIN    = 4;  /* momentary NO to GND */

static const unsigned long LINK_BAUD    = 921600;
static const uint32_t DEBOUNCE_MS       = 30;
static const uint32_t ANNUNCIATOR_MS    = 200;

static SupervisorState state;
static IpcParser parser;
static ArduinoLEDMatrix matrix;
static Latch latch;

static uint32_t last_debounce_ms = 0;
static bool     debounced_button = false;
static bool     last_raw_button  = false;
static uint32_t last_annunciator_ms = 0;
static Annunciator last_glyph = ANN_WATCHING;

/* ------------------------------------------------------------------ */
/* 12x8 LED matrix glyphs                                             */
/* ------------------------------------------------------------------ */

/* Steady outline: watching, nothing wrong. */
static const uint8_t GLYPH_WATCHING[8][12] = {
  {1,1,1,1,1,1,1,1,1,1,1,1},
  {1,0,0,0,0,0,0,0,0,0,0,1},
  {1,0,0,0,0,0,0,0,0,0,0,1},
  {1,0,0,0,0,0,0,0,0,0,0,1},
  {1,0,0,0,0,0,0,0,0,0,0,1},
  {1,0,0,0,0,0,0,0,0,0,0,1},
  {1,0,0,0,0,0,0,0,0,0,0,1},
  {1,1,1,1,1,1,1,1,1,1,1,1},
};

/* Solid block: override latched by the operator. */
static const uint8_t GLYPH_OVERRIDE[8][12] = {
  {1,1,1,1,1,1,1,1,1,1,1,1},
  {1,1,1,1,1,1,1,1,1,1,1,1},
  {1,1,1,1,1,1,1,1,1,1,1,1},
  {1,1,1,1,1,1,1,1,1,1,1,1},
  {1,1,1,1,1,1,1,1,1,1,1,1},
  {1,1,1,1,1,1,1,1,1,1,1,1},
  {1,1,1,1,1,1,1,1,1,1,1,1},
  {1,1,1,1,1,1,1,1,1,1,1,1},
};

/* Broken bar: the governance tier stopped reporting. */
static const uint8_t GLYPH_STALE[8][12] = {
  {0,0,0,0,0,0,0,0,0,0,0,0},
  {1,1,1,0,0,1,1,0,0,1,1,1},
  {1,1,1,0,0,1,1,0,0,1,1,1},
  {0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0,0,0,0,0},
  {1,1,1,0,0,1,1,0,0,1,1,1},
  {1,1,1,0,0,1,1,0,0,1,1,1},
  {0,0,0,0,0,0,0,0,0,0,0,0},
};

/* Cross: the audit digest stream skipped or rewound. */
static const uint8_t GLYPH_ATTEST[8][12] = {
  {1,1,0,0,0,0,0,0,0,0,1,1},
  {0,1,1,0,0,0,0,0,0,1,1,0},
  {0,0,1,1,0,0,0,0,1,1,0,0},
  {0,0,0,1,1,0,0,1,1,0,0,0},
  {0,0,0,1,1,0,0,1,1,0,0,0},
  {0,0,1,1,0,0,0,0,1,1,0,0},
  {0,1,1,0,0,0,0,0,0,1,1,0},
  {1,1,0,0,0,0,0,0,0,0,1,1},
};

/* Split bar: the relay is not where it was told to be. */
static const uint8_t GLYPH_LATCH[8][12] = {
  {1,1,1,1,1,0,0,1,1,1,1,1},
  {1,0,0,0,1,0,0,1,0,0,0,1},
  {1,0,0,0,1,0,0,1,0,0,0,1},
  {1,1,1,1,1,0,0,1,1,1,1,1},
  {0,0,0,0,0,0,0,0,0,0,0,0},
  {1,1,1,1,1,0,0,1,1,1,1,1},
  {1,0,0,0,1,0,0,1,0,0,0,1},
  {1,1,1,1,1,0,0,1,1,1,1,1},
};

static void draw(Annunciator glyph) {
  switch (glyph) {
    case ANN_OVERRIDE: matrix.renderBitmap(GLYPH_OVERRIDE, 8, 12); break;
    case ANN_STALE:    matrix.renderBitmap(GLYPH_STALE,    8, 12); break;
    case ANN_ATTEST:   matrix.renderBitmap(GLYPH_ATTEST,   8, 12); break;
    case ANN_LATCH:    matrix.renderBitmap(GLYPH_LATCH,    8, 12); break;
    default:           matrix.renderBitmap(GLYPH_WATCHING, 8, 12); break;
  }
}

/* ------------------------------------------------------------------ */
/* Transmit helpers                                                    */
/* ------------------------------------------------------------------ */

static uint64_t now_us() {
  return (uint64_t)micros();
}

static void send_override_assert(uint8_t reason) {
  uint8_t frame[IPC_MAX_FRAME];
  const size_t n = ipc_encode_override_assert(frame, now_us(), reason);
  Serial.write(frame, n);
  Serial.flush();
}

static void send_override_clear() {
  uint8_t frame[IPC_MAX_FRAME];
  const size_t n = ipc_encode_override_clear(frame, now_us());
  Serial.write(frame, n);
  Serial.flush();
}

static void send_attest_ack(uint64_t audit_ref, uint8_t verdict) {
  uint8_t frame[IPC_MAX_FRAME];
  const size_t n = ipc_encode_attest_ack(frame, audit_ref, verdict);
  Serial.write(frame, n);
  Serial.flush();
}

/* All three positions, not just the observation. A receiver that saw only
 * where the contact is could not tell one resting where it was asked to rest
 * from one that never moved, which is what the read-back exists to detect.
 * Polls on demand when nothing has been read yet, rather than reporting
 * nothing at all. */
static void send_latch_report(uint32_t now_ms) {
  if (!latch.has_reading) {
    latch_poll(&latch, now_ms);
  }
  uint8_t frame[IPC_MAX_FRAME];
  const size_t n = ipc_encode_latch_report(
      frame, (uint8_t)latch.last.commanded, (uint8_t)latch.last.reported,
      (uint8_t)latch.last.observed, latch.transitions, latch.mismatches);
  Serial.write(frame, n);
  Serial.flush();
}

/* ------------------------------------------------------------------ */
/* Latch relay                                                         */
/* ------------------------------------------------------------------ */

/* SET and RESET are single-register writes to the module, held for the
 * specified pulse. The contact is bistable: once moved it stays without
 * current, which is the property the GPIO line this replaces did not have. */
static void latch_io_pulse_open(void *) {
  Wire.beginTransmission(LATCH_I2C_ADDR);
  Wire.write(0x01);            /* SET: open the contact, cut motor supply */
  Wire.endTransmission();
  delay(LATCH_PULSE_MS);
}

static void latch_io_pulse_close(void *) {
  Wire.beginTransmission(LATCH_I2C_ADDR);
  Wire.write(0x00);            /* RESET: close the contact */
  Wire.endTransmission();
  delay(LATCH_PULSE_MS);
}

/* What the module's own MCU believes. Treated as a cross-check only: it may
 * be echoing the last command rather than observing the contact. */
static LatchPosition latch_io_read_register(void *) {
  Wire.requestFrom(LATCH_I2C_ADDR, 1);
  if (!Wire.available()) {
    return LATCH_UNKNOWN;
  }
  return Wire.read() ? LATCH_OPEN : LATCH_CLOSED;
}

/* The sense circuit on the contact. This is the source of truth.
 *
 * Two channels, wired antivalent, both INPUT_PULLUP and both active low.
 * Channel A is an opto across the contact, so its LED sees the full battery
 * only while the contact is open. Channel B is an opto across the motor rail,
 * so its LED is lit only while the rail is live. In normal operation exactly
 * one of them conducts.
 *
 * A single channel would not do. Whichever way one pin is wired, one of its
 * two readings is also what a cut wire produces, so one contact position
 * becomes indistinguishable from a fault. If that position is OPEN, a broken
 * sense wire reports the motors isolated when nothing at all is known about
 * them, which is the one claim this board must never make. With the pair,
 * a cut harness, a dead opto or a flat battery leaves both channels dark, the
 * readings stop being complementary, and the answer is UNKNOWN. Nothing
 * upstream rounds UNKNOWN up to isolation.
 *
 * Only the energised channel is under test at any instant, so a break in the
 * dark one stays latent until the contact next moves. That is one of the
 * reasons every command reads back and the pair is polled rather than waiting
 * for an edge. */
static LatchPosition latch_io_read_sense(void *) {
  const bool open_channel   = digitalRead(LATCH_SENSE_A_PIN) == LOW;
  const bool closed_channel = digitalRead(LATCH_SENSE_B_PIN) == LOW;
  if (open_channel && !closed_channel) {
    return LATCH_OPEN;
  }
  if (closed_channel && !open_channel) {
    return LATCH_CLOSED;
  }
  return LATCH_UNKNOWN;
}

/* Drive the relay from the latched state on every pass rather than on
 * transitions. A missed transition would otherwise leave the motors powered
 * while the annunciator said otherwise. Idempotent by construction. */
static void drive_latch(uint32_t now_ms) {
  if (supervisor_kill_line(&state)) {
    if (latch.commanded != LATCH_OPEN) {
      latch_enforce_halt(&latch, now_ms);
    }
  } else if (latch.commanded != LATCH_CLOSED) {
    latch_permit(&latch, now_ms);
  }

  /* Poll the contact back at a fixed cadence. A relay that silently failed
   * to move raises no edge, so only a poll finds it. A disagreement between
   * commanded, reported and observed latches an override: a safety contact
   * that is not where it was told to be is a fault in either direction. */
  LatchReading reading;
  if (latch_poll_if_due(&latch, now_ms, &reading)
      && latch.commanded != LATCH_UNKNOWN
      && !latch_reading_agrees(&reading)) {
    send_latch_report(now_ms);
    if (supervisor_assert_override(&state, OVR_LATCH_MISMATCH)) {
      /* Open the contact before announcing it. If the announcement is what
       * fails, the motors are already isolated. */
      latch_enforce_halt(&latch, now_ms);
      send_override_assert(OVR_LATCH_MISMATCH);
      send_latch_report(now_ms);
    }
  }
}

/*
 * A request from the governance tier. This board decides, and the asymmetry
 * is the reason the relay hangs off this bus and not the deciding host's.
 *
 * OPEN is always honoured: more ways to stop are safe. CLOSE is refused
 * outright while an override stands, so nothing on the wire can talk the
 * override down. Either way a report goes back, because a request whose
 * outcome is never reported is the assertion this whole path replaces.
 */
static void on_latch_request(const LatchRequest *req, uint32_t now_ms) {
  const bool refused = state.mode == SUP_OVERRIDE && req->desired == LATCH_CLOSED;
  if (!refused) {
    if (req->desired == LATCH_OPEN) {
      latch_enforce_halt(&latch, now_ms);
    } else if (req->desired == LATCH_CLOSED) {
      latch_permit(&latch, now_ms);
    }
  }
  send_latch_report(now_ms);
}

/* ------------------------------------------------------------------ */
/* Buttons                                                             */
/* ------------------------------------------------------------------ */

static void poll_buttons(uint32_t now_ms) {
  /* NC to GND with INPUT_PULLUP: closed reads LOW, pressed or cut reads HIGH. */
  const bool raw = digitalRead(OVERRIDE_BUTTON_PIN) == HIGH;

  if (raw != last_raw_button) {
    last_raw_button = raw;
    last_debounce_ms = now_ms;
  } else if ((uint32_t)(now_ms - last_debounce_ms) > DEBOUNCE_MS
             && raw != debounced_button) {
    debounced_button = raw;
    if (supervisor_set_button(&state, debounced_button)) {
      send_override_assert(OVR_OPERATOR_BUTTON);
    }
  }

  /* Clear is NO to GND: held low only while pressed. The state machine
   * refuses the clear while the override button is still held or the
   * governance heartbeat is stale, so this cannot paper over a live fault. */
  if (digitalRead(CLEAR_BUTTON_PIN) == LOW && state.mode == SUP_OVERRIDE) {
    if (supervisor_clear_override(&state, now_ms)) {
      send_override_clear();
    }
  }
}

/* ------------------------------------------------------------------ */
/* Wi-Fi console (optional)                                            */
/* ------------------------------------------------------------------ */

#if ENABLE_WIFI_CONSOLE
static void console_begin() {
  WiFi.begin(SECRET_SSID, SECRET_PASS);
  for (uint8_t i = 0; i < 20 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
  }
  console.begin();
}

static void console_poll(uint32_t now_ms) {
  WiFiClient client = console.available();
  if (!client) {
    return;
  }
  const String line = client.readStringUntil('\n');

  if (line.startsWith("OVERRIDE")) {
    if (supervisor_assert_override(&state, OVR_REMOTE_CONSOLE)) {
      send_override_assert(OVR_REMOTE_CONSOLE);
    }
    client.println("override asserted");
  } else if (line.startsWith("CLEAR")) {
    if (supervisor_clear_override(&state, now_ms)) {
      send_override_clear();
      client.println("override cleared");
    } else {
      client.println("refused: cause still present");
    }
  } else {
    client.print("mode=");
    client.print(state.mode == SUP_OVERRIDE ? "OVERRIDE" : "WATCHING");
    client.print(" reason=");
    client.print(state.override_reason);
    client.print(" last_ref=");
    client.print((unsigned long)state.last_ref);
    client.print(" retained=");
    client.print(state.ring_count);
    client.print(" heartbeats=");
    client.print(state.heartbeats_received);
    client.print(" chain_faults=");
    client.println(state.chain_faults);
  }
  client.stop();
}
#endif

/* ------------------------------------------------------------------ */
/* Arduino entry points                                                */
/* ------------------------------------------------------------------ */

void setup() {
  pinMode(OVERRIDE_BUTTON_PIN, INPUT_PULLUP);
  pinMode(CLEAR_BUTTON_PIN, INPUT_PULLUP);
  pinMode(LATCH_SENSE_A_PIN, INPUT_PULLUP);
  pinMode(LATCH_SENSE_B_PIN, INPUT_PULLUP);

  Wire.begin();
  matrix.begin();
  Serial.begin(LINK_BAUD);

  supervisor_init(&state, SUP_HEARTBEAT_TIMEOUT_MS, millis());

  LatchIo io;
  io.pulse_open = latch_io_pulse_open;
  io.pulse_close = latch_io_pulse_close;
  io.read_register = latch_io_read_register;
  io.read_sense = latch_io_read_sense;
  io.ctx = nullptr;
  latch_init(&latch, io);

  /* Isolate the motors before anything else. The contact is bistable, so it
   * comes up wherever it was left and this board must not assume that is
   * where it wants it. The state machine keeps it open until the first
   * heartbeat arrives: a governance tier that has said nothing has not
   * earned the authority to move a robot. */
  latch_enforce_halt(&latch, millis());

  ipc_parser_reset(&parser);
  draw(supervisor_annunciator(&state));

#if ENABLE_WIFI_CONSOLE
  console_begin();
#endif
}

void loop() {
  const uint32_t now_ms = millis();

  /* Inbound frames */
  SupervisorHeartbeat hb;
  AttestDigest digest;
  LatchRequest latch_req;
  while (Serial.available() > 0) {
    const uint8_t hit = ipc_parser_feed(
        &parser, (uint8_t)Serial.read(), &hb, &digest, &latch_req);
    if (hit == MSG_SUPERVISOR_HEARTBEAT) {
      const bool first = !state.heartbeat_seen;
      supervisor_on_heartbeat(&state, &hb, now_ms);
      /* The contact is released on first contact with a live governance tier
       * and the release is reported, not assumed. An override already latched
       * keeps it open: drive_latch below is what actually decides. */
      if (first && state.mode != SUP_OVERRIDE) {
        drive_latch(now_ms);
        send_latch_report(now_ms);
      }
    } else if (hit == MSG_LATCH_REQUEST) {
      on_latch_request(&latch_req, now_ms);
    } else if (hit == MSG_ATTEST_DIGEST) {
      const uint8_t verdict = supervisor_on_digest(&state, &digest);
      send_attest_ack(digest.audit_ref, verdict);
      if (verdict != ATT_CHAIN_OK && state.override_reason == OVR_ATTESTATION_MISMATCH) {
        send_override_assert(OVR_ATTESTATION_MISMATCH);
      }
    }
  }

  poll_buttons(now_ms);

  /* Heartbeat watchdog */
  if (supervisor_tick(&state, now_ms)) {
    send_override_assert(OVR_GOVERNANCE_HEARTBEAT_LOST);
  }

  drive_latch(now_ms);

#if ENABLE_WIFI_CONSOLE
  console_poll(now_ms);
#endif

  /* Repaint at a fixed cadence rather than every pass: the matrix driver is
   * slow enough to matter next to a 921600 baud link. */
  const Annunciator glyph = supervisor_annunciator(&state);
  if (glyph != last_glyph || (uint32_t)(now_ms - last_annunciator_ms) > ANNUNCIATOR_MS) {
    draw(glyph);
    last_glyph = glyph;
    last_annunciator_ms = now_ms;
  }
}
