-- ============================================================================
-- Telegram crawler archive (PostgreSQL). "{schema}" is replaced with TG_DB_SCHEMA (default: crawler).
-- Write order for every message: chat → sender (user or chat) → parent message → message.
-- need_engine reads tg_messages by increasing id (read-only).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS {schema};

-- One row per Telegram user, stored the first time we see them (snapshot, no history).
CREATE TABLE IF NOT EXISTS {schema}.tg_users (
    user_id             BIGINT PRIMARY KEY,
    username            TEXT,
    usernames           TEXT[],                 -- all active (incl. collectible) usernames
    first_name          TEXT,
    last_name           TEXT,
    phone               TEXT,                   -- only when visible to the crawler account
    lang_code           TEXT,
    is_bot              BOOLEAN NOT NULL DEFAULT FALSE,
    is_premium          BOOLEAN NOT NULL DEFAULT FALSE,
    is_verified         BOOLEAN NOT NULL DEFAULT FALSE,
    is_scam             BOOLEAN NOT NULL DEFAULT FALSE,
    is_fake             BOOLEAN NOT NULL DEFAULT FALSE,
    is_deleted          BOOLEAN NOT NULL DEFAULT FALSE,
    bio                 TEXT,                   -- filled once by the profile worker (users.GetFullUser)
    profile_fetched_at  TIMESTAMPTZ,            -- NULL = full profile not fetched yet
    raw                 JSONB,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Groups we monitor + chats that appear as message senders / forward sources.
CREATE TABLE IF NOT EXISTS {schema}.tg_chats (
    chat_id              BIGINT PRIMARY KEY,    -- marked peer id (-100… for supergroups/channels)
    type                 TEXT NOT NULL,         -- supergroup | group | channel | gigagroup
    title                TEXT,
    username             TEXT,
    about                TEXT,
    members_count        INTEGER,
    linked_chat_id       BIGINT,                -- discussion group of a channel / channel of a group
    is_forum             BOOLEAN NOT NULL DEFAULT FALSE,
    is_verified          BOOLEAN NOT NULL DEFAULT FALSE,
    is_scam              BOOLEAN NOT NULL DEFAULT FALSE,
    join_link            TEXT,                  -- the link the crawler was given
    is_monitored         BOOLEAN NOT NULL DEFAULT FALSE,
    last_scanned_msg_id  BIGINT NOT NULL DEFAULT 0,
    last_scanned_at      TIMESTAMPTZ,
    raw                  JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {schema}.tg_messages (
    id                BIGSERIAL PRIMARY KEY,                -- arrival order = need_engine cursor
    chat_id           BIGINT NOT NULL REFERENCES {schema}.tg_chats(chat_id),
    message_id        BIGINT NOT NULL,
    sender_user_id    BIGINT REFERENCES {schema}.tg_users(user_id),
    sender_chat_id    BIGINT REFERENCES {schema}.tg_chats(chat_id),   -- channel posts / anonymous admins
    date              TIMESTAMPTZ NOT NULL,
    edit_date         TIMESTAMPTZ,
    text              TEXT NOT NULL DEFAULT '',             -- message text or media caption
    entities          JSONB,                                -- links, mentions, formatting
    reply_to_msg_id   BIGINT,
    reply_to_top_id   BIGINT,                               -- thread / forum topic root
    quote_text        TEXT,
    fwd_from_id       BIGINT,
    fwd_from_name     TEXT,
    fwd_date          TIMESTAMPTZ,
    via_bot_id        BIGINT,
    post_author       TEXT,
    grouped_id        BIGINT,                               -- album
    media_type        TEXT,                                 -- photo | video | voice | document | sticker | poll | webpage | ...
    media             JSONB,                                -- metadata only (mime, size, name, duration, url, poll…)
    views             INTEGER,
    forwards          INTEGER,
    is_service        BOOLEAN NOT NULL DEFAULT FALSE,
    service_action    TEXT,                                 -- e.g. ChatAddUser, ChatJoinedByLink
    is_pinned         BOOLEAN NOT NULL DEFAULT FALSE,
    is_context        BOOLEAN NOT NULL DEFAULT FALSE,       -- fetched only because a newer message replies to it
    raw               JSONB,
    archived_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS tg_messages_reply_idx  ON {schema}.tg_messages (chat_id, reply_to_msg_id);
CREATE INDEX IF NOT EXISTS tg_messages_sender_idx ON {schema}.tg_messages (sender_user_id);
CREATE INDEX IF NOT EXISTS tg_users_profile_idx   ON {schema}.tg_users (first_seen_at) WHERE profile_fetched_at IS NULL;
