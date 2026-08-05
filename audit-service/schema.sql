-- Governed Edge AI: audit log schema
-- Append-only. The logging service issues no UPDATE or DELETE statements.
-- Stored on a dedicated NVMe SSD, separate from the OS and model volumes.

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,            -- ISO 8601 UTC timestamp
    session_id      TEXT    NOT NULL,            -- UUID per power cycle
    actor           TEXT    NOT NULL             -- 'ai' | 'human_override'
                    CHECK (actor IN ('ai', 'human_override')),
    detection_type  TEXT    NOT NULL             -- 'object' | 'gesture' | 'pose'
                    CHECK (detection_type IN ('object', 'gesture', 'pose')),
    detection_label TEXT    NOT NULL,            -- e.g. 'person', 'thumbs_up', 'proximity_breach'
    confidence      REAL    NOT NULL             -- 0.0 to 1.0
                    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    command         TEXT    NOT NULL,            -- actuation command issued, 'HALT', or 'NONE'
    command_sent    INTEGER NOT NULL             -- 1 = sent to STM32H5, 0 = suppressed
                    CHECK (command_sent IN (0, 1)),
    stm32_ack       INTEGER                      -- 1 = acknowledged, 0 = rejected, NULL = no response
                    CHECK (stm32_ack IN (0, 1) OR stm32_ack IS NULL),
    flag            INTEGER NOT NULL DEFAULT 0   -- 1 = flagged for review (low confidence, drift)
                    CHECK (flag IN (0, 1)),
    notes           TEXT                         -- free-text annotation, human override events only
);

-- Index for time-range queries (dashboard and LLM query interface)
CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_log (ts);

-- Index for actor-based filtering
CREATE INDEX IF NOT EXISTS idx_audit_actor  ON audit_log (actor);

-- Partial index for flagged events
CREATE INDEX IF NOT EXISTS idx_audit_flag   ON audit_log (flag) WHERE flag = 1;

-- Session registry: one row per power cycle
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    board_serial    TEXT,
    notes           TEXT
);
