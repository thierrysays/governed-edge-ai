/*
 * supervisor_state.cpp - oversight state machine for the Arduino UNO R4 WiFi.
 * See supervisor_state.h. Governed Edge AI - Glossolalie Advisory. Apache 2.0.
 */

#include "supervisor_state.h"

#include <string.h>

/* millis() wraps every ~49.7 days. Unsigned subtraction is wrap-safe, so
 * elapsed time is always computed this way and never by comparing absolute
 * timestamps. */
static uint32_t elapsed(uint32_t now_ms, uint32_t then_ms) {
  return (uint32_t)(now_ms - then_ms);
}

void supervisor_init(SupervisorState *s, uint32_t heartbeat_timeout_ms, uint32_t now_ms) {
  memset(s, 0, sizeof(*s));
  s->mode = SUP_WATCHING;
  s->heartbeat_timeout_ms = heartbeat_timeout_ms;
  s->last_heartbeat_ms = now_ms;
  s->heartbeat_seen = false;
}

bool supervisor_assert_override(SupervisorState *s, uint8_t reason) {
  if (s->mode == SUP_OVERRIDE) {
    return false;
  }
  s->mode = SUP_OVERRIDE;
  s->override_reason = reason;
  s->overrides_asserted++;
  return true;
}

bool supervisor_clear_override(SupervisorState *s, uint32_t now_ms) {
  if (s->mode == SUP_WATCHING) {
    return true;
  }
  if (s->button_pressed) {
    return false;
  }
  /* A node that has never heard from the governance tier cannot conclude the
   * tier is healthy, so a clear needs a heartbeat that actually arrived and
   * is still fresh. */
  if (!s->heartbeat_seen || supervisor_heartbeat_stale(s, now_ms)) {
    return false;
  }
  /* Resynchronise the expected reference when clearing an attestation
   * override: otherwise every later digest gaps against a stale expectation
   * and the node can never resume. The gap stays in the retained ring, and
   * the clear is the record that an operator accepted it. */
  if (s->chain_alert && s->heartbeat_seen && s->reported_audit_ref > s->last_ref) {
    s->last_ref = s->reported_audit_ref;
  }
  s->mode = SUP_WATCHING;
  s->override_reason = 0;
  s->chain_alert = false;
  s->overrides_cleared++;
  return true;
}

void supervisor_on_heartbeat(SupervisorState *s, const SupervisorHeartbeat *hb,
                             uint32_t now_ms) {
  s->heartbeats_received++;
  s->heartbeat_seen = true;
  s->last_heartbeat_ms = now_ms;
  s->reported_audit_ref = hb->last_audit_ref;
  s->reported_state     = hb->system_state;
  s->reported_events    = hb->events_logged;
  s->reported_commands  = hb->commands_sent;
}

uint8_t supervisor_on_digest(SupervisorState *s, const AttestDigest *d) {
  s->digests_received++;

  uint8_t verdict;
  if (d->audit_ref <= s->last_ref) {
    verdict = ATT_CHAIN_BREAK;   /* replay or rollback */
  } else if (d->audit_ref > s->last_ref + 1) {
    verdict = ATT_GAP;           /* rows missing from the stream */
  } else {
    verdict = ATT_CHAIN_OK;
  }

  if (verdict == ATT_CHAIN_OK) {
    s->ring_ref[s->ring_head] = d->audit_ref;
    memcpy(s->ring_digest[s->ring_head], d->digest, IPC_DIGEST_BYTES);
    s->ring_head = (uint8_t)((s->ring_head + 1) % SUP_DIGEST_CAPACITY);
    if (s->ring_count < SUP_DIGEST_CAPACITY) {
      s->ring_count++;
    }
    s->last_ref = d->audit_ref;
  } else {
    s->chain_alert = true;
    s->chain_faults++;
    supervisor_assert_override(s, OVR_ATTESTATION_MISMATCH);
  }

  return verdict;
}

bool supervisor_heartbeat_stale(const SupervisorState *s, uint32_t now_ms) {
  return elapsed(now_ms, s->last_heartbeat_ms) > s->heartbeat_timeout_ms;
}

bool supervisor_tick(SupervisorState *s, uint32_t now_ms) {
  if (!supervisor_heartbeat_stale(s, now_ms)) {
    return false;
  }
  return supervisor_assert_override(s, OVR_GOVERNANCE_HEARTBEAT_LOST);
}

bool supervisor_set_button(SupervisorState *s, bool pressed_now) {
  const bool rising = pressed_now && !s->button_pressed;
  s->button_pressed = pressed_now;
  if (!rising) {
    return false;
  }
  return supervisor_assert_override(s, OVR_OPERATOR_BUTTON);
}

Annunciator supervisor_annunciator(const SupervisorState *s) {
  if (s->mode != SUP_OVERRIDE) {
    return ANN_WATCHING;
  }
  if (s->override_reason == OVR_GOVERNANCE_HEARTBEAT_LOST) {
    return ANN_STALE;
  }
  if (s->override_reason == OVR_ATTESTATION_MISMATCH) {
    return ANN_ATTEST;
  }
  return ANN_OVERRIDE;
}

bool supervisor_kill_line(const SupervisorState *s) {
  /* Held while an override is latched, and also before the first heartbeat
   * ever arrives: a governance tier that has not yet said anything has not
   * yet earned the authority to move a robot. No latch, no arming step. */
  return s->mode == SUP_OVERRIDE || !s->heartbeat_seen;
}

bool supervisor_retained(const SupervisorState *s, uint8_t index,
                         uint64_t *audit_ref, uint8_t *digest_out) {
  if (index >= s->ring_count) {
    return false;
  }
  /* Oldest first: when the ring has wrapped, slot ring_head holds the oldest. */
  const uint8_t base = (s->ring_count == SUP_DIGEST_CAPACITY) ? s->ring_head : 0;
  const uint8_t slot = (uint8_t)((base + index) % SUP_DIGEST_CAPACITY);
  *audit_ref = s->ring_ref[slot];
  memcpy(digest_out, s->ring_digest[slot], IPC_DIGEST_BYTES);
  return true;
}
