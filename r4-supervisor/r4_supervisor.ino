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
 *   2. Hard line: KILL_LINE_PIN drives the Alvik kill-switch input. The
 *      Alvik firmware rejects every command while that pin reads active,
 *      so the veto holds even if the VENTUNO Q ignores the soft one.
 *
 * Wiring
 *   D2   OVERRIDE_BUTTON_PIN  momentary NC button to GND, INPUT_PULLUP.
 *                             Normally closed: a cut wire reads as pressed.
 *   D3   KILL_LINE_PIN        output to the Alvik kill-switch input (D4 on
 *                             the Alvik, active low). Common ground required.
 *   USB-C                     serial link to the VENTUNO Q at 921600 baud.
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

#include "Arduino_LED_Matrix.h"
#include "ipc_frame.h"
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
static const uint8_t KILL_LINE_PIN       = 3;
static const uint8_t CLEAR_BUTTON_PIN    = 4;  /* momentary NO to GND */

static const unsigned long LINK_BAUD    = 921600;
static const uint32_t DEBOUNCE_MS       = 30;
static const uint32_t ANNUNCIATOR_MS    = 200;

static SupervisorState state;
static IpcParser parser;
static ArduinoLEDMatrix matrix;

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

static void draw(Annunciator glyph) {
  switch (glyph) {
    case ANN_OVERRIDE: matrix.renderBitmap(GLYPH_OVERRIDE, 8, 12); break;
    case ANN_STALE:    matrix.renderBitmap(GLYPH_STALE,    8, 12); break;
    case ANN_ATTEST:   matrix.renderBitmap(GLYPH_ATTEST,   8, 12); break;
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

/* The kill line is driven from the latch on every pass, not toggled on
 * transitions. A missed transition would otherwise leave the actuator
 * enabled while the annunciator says otherwise. */
static void drive_kill_line() {
  digitalWrite(KILL_LINE_PIN, supervisor_kill_line(&state) ? LOW : HIGH);
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
  pinMode(KILL_LINE_PIN, OUTPUT);

  /* Hold the kill line before anything else, and keep holding it: the state
   * machine reports the line asserted until the first heartbeat arrives, so
   * the rig cannot move before the governance tier has said anything. No
   * latch is involved and no arming step is needed. */
  digitalWrite(KILL_LINE_PIN, LOW);

  matrix.begin();
  Serial.begin(LINK_BAUD);

  supervisor_init(&state, SUP_HEARTBEAT_TIMEOUT_MS, millis());
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
  while (Serial.available() > 0) {
    const uint8_t hit = ipc_parser_feed(&parser, (uint8_t)Serial.read(), &hb, &digest);
    if (hit == MSG_SUPERVISOR_HEARTBEAT) {
      supervisor_on_heartbeat(&state, &hb, now_ms);
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

  drive_kill_line();

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
