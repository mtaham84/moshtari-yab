-- ============================================================================
-- Schema for Telegram Lead Crawler
-- Stores discovered leads and monitoring state
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS leads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id        TEXT UNIQUE NOT NULL,
    group_id       INTEGER NOT NULL,
    group_title    TEXT NOT NULL,
    target_msg_id  INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    user_name      TEXT,
    user_username  TEXT,
    target_text    TEXT NOT NULL,
    target_date    TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    is_synced      INTEGER DEFAULT 0,
    synced_at      TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_leads_group_id ON leads(group_id);
CREATE INDEX IF NOT EXISTS idx_leads_user_id ON leads(user_id);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at);

CREATE TABLE IF NOT EXISTS group_monitors (
    group_id             INTEGER PRIMARY KEY,
    title                TEXT,
    link                 TEXT,
    last_scanned_msg_id  INTEGER DEFAULT 0,
    last_scanned_at      TEXT,
    is_active            INTEGER DEFAULT 1,
    created_at           TEXT DEFAULT (datetime('now'))
);

-- Every message seen in a monitored group (not only candidates).
-- Used to build conversation context locally (fewer Telegram API calls),
-- to resume scans, and as raw material for the labelled dataset.
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
    is_candidate     INTEGER DEFAULT 0,
    enqueued         INTEGER DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(group_id, msg_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_group_msg ON messages(group_id, msg_id);
CREATE INDEX IF NOT EXISTS idx_messages_reply ON messages(group_id, reply_to_msg_id);
