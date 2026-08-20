/*
 * ipc_frame.cpp - IPC frame codec, oversight subset.
 * See ipc_frame.h. Governed Edge AI - Glossolalie Advisory. Apache 2.0.
 */

#include "ipc_frame.h"

#include <string.h>

#define CRC_POLY 0x1021
#define CRC_INIT 0xFFFF

uint16_t ipc_crc16_ccitt(const uint8_t *data, size_t len) {
  uint16_t crc = CRC_INIT;
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ CRC_POLY) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

/* ------------------------------------------------------------------ */
/* Little-endian helpers                                              */
/* ------------------------------------------------------------------ */

static void put_u16(uint8_t *out, uint16_t v) {
  out[0] = (uint8_t)(v & 0xFF);
  out[1] = (uint8_t)((v >> 8) & 0xFF);
}

static void put_u32(uint8_t *out, uint32_t v) {
  for (uint8_t i = 0; i < 4; i++) {
    out[i] = (uint8_t)((v >> (8 * i)) & 0xFF);
  }
}

static void put_u64(uint8_t *out, uint64_t v) {
  for (uint8_t i = 0; i < 8; i++) {
    out[i] = (uint8_t)((v >> (8 * i)) & 0xFF);
  }
}

static uint16_t get_u16(const uint8_t *in) {
  return (uint16_t)(in[0] | ((uint16_t)in[1] << 8));
}

static uint32_t get_u32(const uint8_t *in) {
  return (uint32_t)in[0] | ((uint32_t)in[1] << 8)
       | ((uint32_t)in[2] << 16) | ((uint32_t)in[3] << 24);
}

static uint64_t get_u64(const uint8_t *in) {
  uint64_t v = 0;
  for (uint8_t i = 0; i < 8; i++) {
    v |= (uint64_t)in[i] << (8 * i);
  }
  return v;
}

/* ------------------------------------------------------------------ */
/* Encoders                                                            */
/* ------------------------------------------------------------------ */

static size_t finish(uint8_t *out, uint8_t type, size_t payload_len) {
  out[0] = IPC_MAGIC;
  out[1] = type;
  put_u16(&out[2], (uint16_t)payload_len);
  uint16_t crc = ipc_crc16_ccitt(out, 4 + payload_len);
  put_u16(&out[4 + payload_len], crc);
  return 4 + payload_len + 2;
}

size_t ipc_encode_override_assert(uint8_t *out, uint64_t timestamp_us, uint8_t reason) {
  put_u64(&out[4], timestamp_us);
  out[12] = reason;
  return finish(out, MSG_OVERRIDE_ASSERT, 9);
}

size_t ipc_encode_override_clear(uint8_t *out, uint64_t timestamp_us) {
  put_u64(&out[4], timestamp_us);
  return finish(out, MSG_OVERRIDE_CLEAR, 8);
}

size_t ipc_encode_attest_ack(uint8_t *out, uint64_t audit_ref, uint8_t verdict) {
  put_u64(&out[4], audit_ref);
  out[12] = verdict;
  return finish(out, MSG_ATTEST_ACK, 9);
}

/* All three positions travel, not just the observation. A receiver that saw
 * only `observed` could not tell a contact resting where it was asked to rest
 * from one that never moved, which is the whole point of the read-back. */
size_t ipc_encode_latch_report(uint8_t *out, uint8_t commanded, uint8_t reported,
                               uint8_t observed, uint32_t transitions,
                               uint32_t mismatches) {
  out[4] = commanded;
  out[5] = reported;
  out[6] = observed;
  put_u32(&out[7], transitions);
  put_u32(&out[11], mismatches);
  return finish(out, MSG_LATCH_REPORT, 11);
}

/* ------------------------------------------------------------------ */
/* Incremental parser                                                  */
/* ------------------------------------------------------------------ */

void ipc_parser_reset(IpcParser *p) {
  p->len = 0;
  p->expected = 0;
}

static uint8_t decode_complete(IpcParser *p, SupervisorHeartbeat *hb,
                               AttestDigest *digest, LatchRequest *latch_req) {
  const uint8_t type = p->buf[1];
  const uint16_t payload_len = get_u16(&p->buf[2]);
  const uint8_t *payload = &p->buf[4];

  const uint16_t crc_rx = get_u16(&p->buf[4 + payload_len]);
  if (crc_rx != ipc_crc16_ccitt(p->buf, (size_t)(4 + payload_len))) {
    return 0;  /* corrupt frame: drop it, the sender will heartbeat again */
  }

  if (type == MSG_SUPERVISOR_HEARTBEAT && payload_len == 17) {
    hb->last_audit_ref = get_u64(&payload[0]);
    hb->system_state   = payload[8];
    hb->events_logged  = get_u32(&payload[9]);
    hb->commands_sent  = get_u32(&payload[13]);
    return MSG_SUPERVISOR_HEARTBEAT;
  }

  if (type == MSG_ATTEST_DIGEST && payload_len == 40) {
    digest->audit_ref = get_u64(&payload[0]);
    memcpy(digest->digest, &payload[8], IPC_DIGEST_BYTES);
    return MSG_ATTEST_DIGEST;
  }

  if (type == MSG_LATCH_REQUEST && payload_len == 9) {
    latch_req->audit_ref = get_u64(&payload[0]);
    latch_req->desired   = payload[8];
    return MSG_LATCH_REQUEST;
  }

  return 0;  /* well-formed but not a message this board acts on */
}

uint8_t ipc_parser_feed(IpcParser *p, uint8_t byte,
                        SupervisorHeartbeat *hb, AttestDigest *digest,
                        LatchRequest *latch_req) {
  if (p->len == 0 && byte != IPC_MAGIC) {
    return 0;  /* resynchronising: discard until the magic byte */
  }

  if (p->len >= IPC_MAX_FRAME) {
    ipc_parser_reset(p);
    return 0;
  }

  p->buf[p->len++] = byte;

  if (p->len == 4) {
    const uint16_t payload_len = get_u16(&p->buf[2]);
    p->expected = (size_t)(4 + payload_len + 2);
    if (p->expected > IPC_MAX_FRAME) {
      /* Longer than anything this board accepts: drop and resynchronise. */
      ipc_parser_reset(p);
      return 0;
    }
  }

  if (p->expected == 0 || p->len < p->expected) {
    return 0;
  }

  const uint8_t result = decode_complete(p, hb, digest, latch_req);
  ipc_parser_reset(p);
  return result;
}
