"""
Lightweight async SQLite wrapper storing per-guild welcomer configuration.
One row per guild, created lazily on first access with sane defaults.
"""
import aiosqlite
from config import (
    DB_PATH,
    DEFAULT_WELCOME_MESSAGE,
    DEFAULT_LEAVE_MESSAGE,
    DEFAULT_DM_MESSAGE,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id            INTEGER PRIMARY KEY,
    welcome_channel_id  INTEGER,
    welcome_enabled     INTEGER NOT NULL DEFAULT 0,
    welcome_message     TEXT NOT NULL,
    welcome_card        INTEGER NOT NULL DEFAULT 1,
    welcome_bg_url      TEXT,

    leave_channel_id    INTEGER,
    leave_enabled       INTEGER NOT NULL DEFAULT 0,
    leave_message       TEXT NOT NULL,

    dm_enabled          INTEGER NOT NULL DEFAULT 0,
    dm_message          TEXT NOT NULL,

    autorole_ids        TEXT NOT NULL DEFAULT '',
    ignored_channel_ids TEXT NOT NULL DEFAULT '',
    custom_prefix       TEXT,

    stay_247            INTEGER NOT NULL DEFAULT 0,
    stay_247_channel_id INTEGER,

    embed_color         INTEGER,
    total_joins         INTEGER NOT NULL DEFAULT 0,
    total_leaves        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_admins (
    user_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS afk_users (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    reason   TEXT NOT NULL,
    since    TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS noprefix_users (
    user_id    INTEGER PRIMARY KEY,
    added_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS noprefix_guilds (
    guild_id   INTEGER PRIMARY KEY,
    added_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS premium_users (
    user_id    INTEGER PRIMARY KEY,
    added_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS premium_guilds (
    guild_id   INTEGER PRIMARY KEY,
    added_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- anti-nuke ----------
CREATE TABLE IF NOT EXISTS antinuke_settings (
    guild_id       INTEGER PRIMARY KEY,
    enabled        INTEGER NOT NULL DEFAULT 0,
    protections    TEXT NOT NULL DEFAULT '',   -- comma list of enabled protection keys
    wall_role_id   INTEGER,
    log_channel_id INTEGER,
    punishment     TEXT NOT NULL DEFAULT 'ban' -- 'ban' or 'strip_roles'
);

CREATE TABLE IF NOT EXISTS antinuke_whitelist (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    bypass   TEXT NOT NULL DEFAULT 'all',  -- 'all' or a comma list of protection keys
    PRIMARY KEY (guild_id, user_id)
);

-- Lightweight structural snapshot used to restore channels/roles a nuker
-- deletes. Refreshed on every relevant create/delete/update, so it's never
-- more than one change stale.
CREATE TABLE IF NOT EXISTS antinuke_snapshots (
    guild_id     INTEGER PRIMARY KEY,
    channels_json TEXT NOT NULL DEFAULT '[]',
    roles_json    TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- automod ----------
CREATE TABLE IF NOT EXISTS automod_settings (
    guild_id            INTEGER PRIMARY KEY,
    log_channel_id      INTEGER,
    mass_mention_limit  INTEGER NOT NULL DEFAULT 3,   -- distinct users pinged in one message
    mass_mention_action TEXT NOT NULL DEFAULT 'mute',
    mute_seconds        INTEGER NOT NULL DEFAULT 600, -- 10 min default timeout
    ip_filter_enabled   INTEGER NOT NULL DEFAULT 1,
    duplicate_limit     INTEGER NOT NULL DEFAULT 3,   -- repeats before action
    duplicate_window    INTEGER NOT NULL DEFAULT 30   -- seconds
);

CREATE TABLE IF NOT EXISTS blacklist_words (
    guild_id INTEGER NOT NULL,
    word     TEXT NOT NULL,
    PRIMARY KEY (guild_id, word)
);

-- ---------- moderation ----------
CREATE TABLE IF NOT EXISTS warnings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason    TEXT NOT NULL DEFAULT 'No reason given',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jail_settings (
    guild_id      INTEGER PRIMARY KEY,
    jail_role_id  INTEGER,
    jail_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS jailed_users (
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    previous_roles TEXT NOT NULL DEFAULT '',
    jailed_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id)
);
"""

_DEFAULTS = {
    "welcome_channel_id": None,
    "welcome_enabled": 0,
    "welcome_message": DEFAULT_WELCOME_MESSAGE,
    "welcome_card": 1,
    "welcome_bg_url": None,
    "leave_channel_id": None,
    "leave_enabled": 0,
    "leave_message": DEFAULT_LEAVE_MESSAGE,
    "dm_enabled": 0,
    "dm_message": DEFAULT_DM_MESSAGE,
    "autorole_ids": "",
    "ignored_channel_ids": "",
    "custom_prefix": None,
    "stay_247": 0,
    "stay_247_channel_id": None,
    "embed_color": None,
    "total_joins": 0,
    "total_leaves": 0,
}


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)  # executescript, not execute — SCHEMA has multiple statements
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def _ensure_row(self, guild_id: int):
        await self._conn.execute(
            "INSERT OR IGNORE INTO guild_settings (guild_id, welcome_message, leave_message, dm_message) "
            "VALUES (?, ?, ?, ?)",
            (guild_id, DEFAULT_WELCOME_MESSAGE, DEFAULT_LEAVE_MESSAGE, DEFAULT_DM_MESSAGE),
        )
        await self._conn.commit()

    async def get_settings(self, guild_id: int) -> dict:
        await self._ensure_row(guild_id)
        cur = await self._conn.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        return dict(row)

    async def update(self, guild_id: int, **fields):
        await self._ensure_row(guild_id)
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self._conn.execute(
            f"UPDATE guild_settings SET {cols} WHERE guild_id = ?", values
        )
        await self._conn.commit()

    async def increment(self, guild_id: int, column: str, by: int = 1):
        await self._ensure_row(guild_id)
        await self._conn.execute(
            f"UPDATE guild_settings SET {column} = {column} + ? WHERE guild_id = ?",
            (by, guild_id),
        )
        await self._conn.commit()

    async def add_autorole(self, guild_id: int, role_id: int):
        settings = await self.get_settings(guild_id)
        ids = [r for r in settings["autorole_ids"].split(",") if r]
        if str(role_id) not in ids:
            ids.append(str(role_id))
        await self.update(guild_id, autorole_ids=",".join(ids))

    async def remove_autorole(self, guild_id: int, role_id: int):
        settings = await self.get_settings(guild_id)
        ids = [r for r in settings["autorole_ids"].split(",") if r]
        if str(role_id) in ids:
            ids.remove(str(role_id))
        await self.update(guild_id, autorole_ids=",".join(ids))

    @staticmethod
    def role_ids(settings: dict) -> list[int]:
        return [int(r) for r in settings["autorole_ids"].split(",") if r]

    # ---------- ignored channels (mention-commands are skipped there) ----------
    async def add_ignored_channel(self, guild_id: int, channel_id: int):
        settings = await self.get_settings(guild_id)
        ids = [c for c in settings["ignored_channel_ids"].split(",") if c]
        if str(channel_id) not in ids:
            ids.append(str(channel_id))
        await self.update(guild_id, ignored_channel_ids=",".join(ids))

    async def remove_ignored_channel(self, guild_id: int, channel_id: int):
        settings = await self.get_settings(guild_id)
        ids = [c for c in settings["ignored_channel_ids"].split(",") if c]
        if str(channel_id) in ids:
            ids.remove(str(channel_id))
        await self.update(guild_id, ignored_channel_ids=",".join(ids))

    @staticmethod
    def ignored_channel_ids(settings: dict) -> list[int]:
        return [int(c) for c in settings["ignored_channel_ids"].split(",") if c]

    async def list_247_guilds(self) -> list[dict]:
        """Guilds with 24/7 enabled — used to auto-rejoin their voice channel on startup."""
        cur = await self._conn.execute(
            "SELECT guild_id, stay_247_channel_id FROM guild_settings WHERE stay_247 = 1 AND stay_247_channel_id IS NOT NULL"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---------- bot admins (a global allowlist, separate from Discord server permissions) ----------
    async def add_bot_admin(self, user_id: int):
        await self._conn.execute("INSERT OR IGNORE INTO bot_admins (user_id) VALUES (?)", (user_id,))
        await self._conn.commit()

    async def remove_bot_admin(self, user_id: int):
        await self._conn.execute("DELETE FROM bot_admins WHERE user_id = ?", (user_id,))
        await self._conn.commit()

    async def list_bot_admins(self) -> list[int]:
        cur = await self._conn.execute("SELECT user_id FROM bot_admins")
        rows = await cur.fetchall()
        return [r["user_id"] for r in rows]

    async def is_bot_admin(self, user_id: int) -> bool:
        cur = await self._conn.execute("SELECT 1 FROM bot_admins WHERE user_id = ?", (user_id,))
        return (await cur.fetchone()) is not None

    # ---------- AFK ----------
    async def set_afk(self, guild_id: int, user_id: int, reason: str):
        import datetime
        await self._conn.execute(
            "INSERT INTO afk_users (guild_id, user_id, reason, since) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET reason = excluded.reason, since = excluded.since",
            (guild_id, user_id, reason, datetime.datetime.utcnow().isoformat()),
        )
        await self._conn.commit()

    async def get_afk(self, guild_id: int, user_id: int) -> dict | None:
        cur = await self._conn.execute(
            "SELECT * FROM afk_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def clear_afk(self, guild_id: int, user_id: int):
        await self._conn.execute("DELETE FROM afk_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        await self._conn.commit()

    # ---------- noprefix (trusted users/guilds can invoke commands with zero prefix) ----------
    async def add_noprefix_user(self, user_id: int):
        await self._conn.execute("INSERT OR IGNORE INTO noprefix_users (user_id) VALUES (?)", (user_id,))
        await self._conn.commit()

    async def remove_noprefix_user(self, user_id: int):
        await self._conn.execute("DELETE FROM noprefix_users WHERE user_id = ?", (user_id,))
        await self._conn.commit()

    async def list_noprefix_users(self) -> list[int]:
        cur = await self._conn.execute("SELECT user_id FROM noprefix_users")
        return [r["user_id"] for r in await cur.fetchall()]

    async def is_noprefix_user(self, user_id: int) -> bool:
        cur = await self._conn.execute("SELECT 1 FROM noprefix_users WHERE user_id = ?", (user_id,))
        return (await cur.fetchone()) is not None

    async def add_noprefix_guild(self, guild_id: int):
        await self._conn.execute("INSERT OR IGNORE INTO noprefix_guilds (guild_id) VALUES (?)", (guild_id,))
        await self._conn.commit()

    async def remove_noprefix_guild(self, guild_id: int):
        await self._conn.execute("DELETE FROM noprefix_guilds WHERE guild_id = ?", (guild_id,))
        await self._conn.commit()

    async def list_noprefix_guilds(self) -> list[int]:
        cur = await self._conn.execute("SELECT guild_id FROM noprefix_guilds")
        return [r["guild_id"] for r in await cur.fetchall()]

    async def is_noprefix_guild(self, guild_id: int) -> bool:
        cur = await self._conn.execute("SELECT 1 FROM noprefix_guilds WHERE guild_id = ?", (guild_id,))
        return (await cur.fetchone()) is not None

    # ---------- premium (tracking/allowlist only — see note in owner.py) ----------
    async def add_premium_user(self, user_id: int):
        await self._conn.execute("INSERT OR IGNORE INTO premium_users (user_id) VALUES (?)", (user_id,))
        await self._conn.commit()

    async def remove_premium_user(self, user_id: int):
        await self._conn.execute("DELETE FROM premium_users WHERE user_id = ?", (user_id,))
        await self._conn.commit()

    async def list_premium_users(self) -> list[dict]:
        cur = await self._conn.execute("SELECT user_id, added_at FROM premium_users ORDER BY added_at")
        return [dict(r) for r in await cur.fetchall()]

    async def is_premium_user(self, user_id: int) -> bool:
        cur = await self._conn.execute("SELECT 1 FROM premium_users WHERE user_id = ?", (user_id,))
        return (await cur.fetchone()) is not None

    async def add_premium_guild(self, guild_id: int):
        await self._conn.execute("INSERT OR IGNORE INTO premium_guilds (guild_id) VALUES (?)", (guild_id,))
        await self._conn.commit()

    async def remove_premium_guild(self, guild_id: int):
        await self._conn.execute("DELETE FROM premium_guilds WHERE guild_id = ?", (guild_id,))
        await self._conn.commit()

    async def list_premium_guilds(self) -> list[dict]:
        cur = await self._conn.execute("SELECT guild_id, added_at FROM premium_guilds ORDER BY added_at")
        return [dict(r) for r in await cur.fetchall()]

    async def is_premium_guild(self, guild_id: int) -> bool:
        cur = await self._conn.execute("SELECT 1 FROM premium_guilds WHERE guild_id = ?", (guild_id,))
        return (await cur.fetchone()) is not None

    # ---------- anti-nuke ----------
    async def get_antinuke(self, guild_id: int) -> dict:
        await self._conn.execute(
            "INSERT OR IGNORE INTO antinuke_settings (guild_id) VALUES (?)", (guild_id,)
        )
        await self._conn.commit()
        cur = await self._conn.execute("SELECT * FROM antinuke_settings WHERE guild_id = ?", (guild_id,))
        return dict(await cur.fetchone())

    async def update_antinuke(self, guild_id: int, **fields):
        await self.get_antinuke(guild_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self._conn.execute(f"UPDATE antinuke_settings SET {cols} WHERE guild_id = ?", values)
        await self._conn.commit()

    @staticmethod
    def protection_list(settings: dict) -> list[str]:
        return [p for p in settings["protections"].split(",") if p]

    async def add_whitelist(self, guild_id: int, user_id: int, bypass: str = "all"):
        await self._conn.execute(
            "INSERT INTO antinuke_whitelist (guild_id, user_id, bypass) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET bypass = excluded.bypass",
            (guild_id, user_id, bypass),
        )
        await self._conn.commit()

    async def remove_whitelist(self, guild_id: int, user_id: int):
        await self._conn.execute(
            "DELETE FROM antinuke_whitelist WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        await self._conn.commit()

    async def list_whitelist(self, guild_id: int) -> list[dict]:
        cur = await self._conn.execute(
            "SELECT user_id, bypass FROM antinuke_whitelist WHERE guild_id = ?", (guild_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def is_whitelisted(self, guild_id: int, user_id: int, protection: str) -> bool:
        cur = await self._conn.execute(
            "SELECT bypass FROM antinuke_whitelist WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        row = await cur.fetchone()
        if not row:
            return False
        if row["bypass"] == "all":
            return True
        return protection in row["bypass"].split(",")

    async def save_snapshot(self, guild_id: int, channels_json: str, roles_json: str):
        await self._conn.execute(
            "INSERT INTO antinuke_snapshots (guild_id, channels_json, roles_json, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(guild_id) DO UPDATE SET channels_json=excluded.channels_json, "
            "roles_json=excluded.roles_json, updated_at=excluded.updated_at",
            (guild_id, channels_json, roles_json),
        )
        await self._conn.commit()

    async def get_snapshot(self, guild_id: int) -> dict | None:
        cur = await self._conn.execute(
            "SELECT * FROM antinuke_snapshots WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    # ---------- automod ----------
    async def get_automod(self, guild_id: int) -> dict:
        await self._conn.execute(
            "INSERT OR IGNORE INTO automod_settings (guild_id) VALUES (?)", (guild_id,)
        )
        await self._conn.commit()
        cur = await self._conn.execute("SELECT * FROM automod_settings WHERE guild_id = ?", (guild_id,))
        return dict(await cur.fetchone())

    async def update_automod(self, guild_id: int, **fields):
        await self.get_automod(guild_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self._conn.execute(f"UPDATE automod_settings SET {cols} WHERE guild_id = ?", values)
        await self._conn.commit()

    async def add_blacklist_word(self, guild_id: int, word: str):
        await self._conn.execute(
            "INSERT OR IGNORE INTO blacklist_words (guild_id, word) VALUES (?, ?)", (guild_id, word.lower())
        )
        await self._conn.commit()

    async def remove_blacklist_word(self, guild_id: int, word: str):
        await self._conn.execute(
            "DELETE FROM blacklist_words WHERE guild_id = ? AND word = ?", (guild_id, word.lower())
        )
        await self._conn.commit()

    async def list_blacklist_words(self, guild_id: int) -> list[str]:
        cur = await self._conn.execute("SELECT word FROM blacklist_words WHERE guild_id = ?", (guild_id,))
        return [r["word"] for r in await cur.fetchall()]

    # ---------- moderation: warnings ----------
    async def add_warning(self, guild_id: int, user_id: int, moderator_id: int, reason: str):
        await self._conn.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, reason),
        )
        await self._conn.commit()

    async def list_warnings(self, guild_id: int, user_id: int) -> list[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY created_at", (guild_id, user_id)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def clear_warnings(self, guild_id: int, user_id: int):
        await self._conn.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        await self._conn.commit()

    # ---------- moderation: jail ----------
    async def get_jail_settings(self, guild_id: int) -> dict:
        await self._conn.execute("INSERT OR IGNORE INTO jail_settings (guild_id) VALUES (?)", (guild_id,))
        await self._conn.commit()
        cur = await self._conn.execute("SELECT * FROM jail_settings WHERE guild_id = ?", (guild_id,))
        return dict(await cur.fetchone())

    async def update_jail_settings(self, guild_id: int, **fields):
        await self.get_jail_settings(guild_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self._conn.execute(f"UPDATE jail_settings SET {cols} WHERE guild_id = ?", values)
        await self._conn.commit()

    async def set_jailed(self, guild_id: int, user_id: int, previous_roles: str):
        await self._conn.execute(
            "INSERT INTO jailed_users (guild_id, user_id, previous_roles) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET previous_roles = excluded.previous_roles",
            (guild_id, user_id, previous_roles),
        )
        await self._conn.commit()

    async def get_jailed(self, guild_id: int, user_id: int) -> dict | None:
        cur = await self._conn.execute(
            "SELECT * FROM jailed_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def clear_jailed(self, guild_id: int, user_id: int):
        await self._conn.execute(
            "DELETE FROM jailed_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        await self._conn.commit()

    async def list_jailed(self, guild_id: int) -> list[int]:
        cur = await self._conn.execute("SELECT user_id FROM jailed_users WHERE guild_id = ?", (guild_id,))
        return [r["user_id"] for r in await cur.fetchall()]


db = Database()
