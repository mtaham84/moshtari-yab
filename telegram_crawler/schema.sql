-- ============================================================================
-- Schema of the Telegram crawler archive.
-- The crawler only archives raw messages; need_engine reads them (read-only) and does the analysis.
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS group_monitors (
    group_id             INTEGER PRIMARY KEY,
    title                TEXT,
    link                 TEXT,
    last_scanned_msg_id  INTEGER DEFAULT 0,
    last_scanned_at      TEXT,
    is_active            INTEGER DEFAULT 1,
    created_at           TEXT DEFAULT (datetime('now'))
);

-- Every message seen in a monitored group. need_engine reads this table by increasing id.
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id         INTEGER NOT NULL,
    msg_id           INTEGER NOT NULL,
    sender_id        INTEGER,
    sender_name      TEXT,
    sender_username  TEXT,
    sender_is_bot    INTEGER DEFAULT 0,
    text             TEXT NOT NULL DEFAULT '',
    date             TEXT NOT NULL,
    reply_to_msg_id  INTEGER,
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(group_id, msg_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_group_msg ON messages(group_id, msg_id);
CREATE INDEX IF NOT EXISTS idx_messages_reply ON messages(group_id, reply_to_msg_id);
