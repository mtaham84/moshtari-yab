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
