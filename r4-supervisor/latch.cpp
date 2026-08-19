/*
 * latch.cpp - Modulino Latch Relay driver.
 * See latch.h. Governed Edge AI - Glossolalie Advisory. Apache 2.0.
 */

#include "latch.h"

#include <string.h>

/* millis() wraps every ~49.7 days; unsigned subtraction is wrap-safe. */
static uint32_t elapsed(uint32_t now_ms, uint32_t then_ms) {
  return (uint32_t)(now_ms - then_ms);
}

void latch_init(Latch *l, LatchIo io) {
  memset(l, 0, sizeof(*l));
  l->io = io;
  /* The arbiter has commanded nothing and does not pretend otherwise. The
   * contact is bistable, so it comes up wherever it was left, and the first
   * poll finds out rather than assuming. */
  l->commanded = LATCH_UNKNOWN;
  l->has_reading = false;
}

bool latch_reading_agrees(const LatchReading *r) {
  return r->observed != LATCH_UNKNOWN
      && r->commanded == r->observed
      && r->commanded == r->reported;
}

LatchReading latch_poll(Latch *l, uint32_t now_ms) {
  LatchReading r;
  r.commanded = l->commanded;
  r.reported = l->io.read_register(l->io.ctx);
  r.observed = l->io.read_sense(l->io.ctx);

  l->last = r;
  l->has_reading = true;
  l->last_poll_ms = now_ms;

  if (l->commanded != LATCH_UNKNOWN && !latch_reading_agrees(&r)) {
    l->mismatches++;
  }
  return r;
}

bool latch_poll_if_due(Latch *l, uint32_t now_ms, LatchReading *out) {
  if (l->has_reading && elapsed(now_ms, l->last_poll_ms) < LATCH_POLL_INTERVAL_MS) {
    return false;
  }
  const LatchReading r = latch_poll(l, now_ms);
  if (out) {
    *out = r;
  }
  return true;
}

static LatchReading latch_command(Latch *l, LatchPosition target, uint32_t now_ms) {
  if (l->commanded != target) {
    l->transitions++;
  }
  l->commanded = target;
  if (target == LATCH_OPEN) {
    l->io.pulse_open(l->io.ctx);
  } else {
    l->io.pulse_close(l->io.ctx);
  }
  /* Read back immediately. A command whose effect is never checked is the
   * assertion this driver exists to replace. */
  return latch_poll(l, now_ms);
}

LatchReading latch_enforce_halt(Latch *l, uint32_t now_ms) {
  return latch_command(l, LATCH_OPEN, now_ms);
}

LatchReading latch_permit(Latch *l, uint32_t now_ms) {
  return latch_command(l, LATCH_CLOSED, now_ms);
}

bool latch_enforcing(const Latch *l) {
  return l->has_reading && l->last.observed == LATCH_OPEN;
}
