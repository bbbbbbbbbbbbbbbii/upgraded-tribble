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

-- ---------- Automod ----------
CREATE TABLE IF NOT EXISTS automod_settings (
    guild_id            INTEGER PRIMARY KEY,
    antilink            INTEGER NOT NULL DEFAULT 0,
    antispam            INTEGER NOT NULL DEFAULT 0,
    antitoken           INTEGER NOT NULL DEFAULT 0,
    anticaps            INTEGER NOT NULL DEFAULT 0,
    anticaps_min_len    INTEGER NOT NULL DEFAULT 5,
    mute_seconds        INTEGER NOT NULL DEFAULT 600,
    log_channel_id      INTEGER,
    whitelist_channel_ids TEXT NOT NULL DEFAULT '',
    whitelist_role_ids  TEXT NOT NULL DEFAULT ''
);

-- ---------- VoiceMaster (Join to Create) ----------
CREATE TABLE IF NOT EXISTS voicemaster_settings (
    guild_id        INTEGER PRIMARY KEY,
    join_channel_id INTEGER,
    category_id     INTEGER,
    panel_channel_id  INTEGER,
    panel_message_id  INTEGER,
    name_template   TEXT NOT NULL DEFAULT '{user}''s Channel'
);

CREATE TABLE IF NOT EXISTS voicemaster_channels (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    owner_id   INTEGER NOT NULL
);

-- ---------- Reaction Roles ----------
CREATE TABLE IF NOT EXISTS reaction_roles (
    guild_id   INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    emoji      TEXT NOT NULL,
    role_id    INTEGER NOT NULL,
    PRIMARY KEY (message_id, emoji)
);

-- ---------- Voice join/leave logging ----------
CREATE TABLE IF NOT EXISTS voicelog_settings (
    guild_id   INTEGER PRIMARY KEY,
    channel_id INTEGER,
    enabled    INTEGER NOT NULL DEFAULT 0
);

-- ---------- VC role (role auto-granted while connected to a specific VC) ----------
CREATE TABLE IF NOT EXISTS vcrole_map (
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

-- ---------- Voice moderators (allowed to use /voice commands beyond Discord perms) ----------
CREATE TABLE IF NOT EXISTS vcmods (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

-- ---------- Voice bans (blocked from joining any voice channel) ----------
CREATE TABLE IF NOT EXISTS vcbans (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
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

    # ---------- Automod ----------
    _AUTOMOD_DEFAULTS = {
        "antilink": 0, "antispam": 0, "antitoken": 0, "mute_seconds": 600,
        "log_channel_id": None, "whitelist_channel_ids": "", "whitelist_role_ids": "",
    }

    async def get_automod(self, guild_id: int) -> dict:
        await self._conn.execute(
            "INSERT OR IGNORE INTO automod_settings (guild_id) VALUES (?)", (guild_id,)
        )
        await self._conn.commit()
        cur = await self._conn.execute("SELECT * FROM automod_settings WHERE guild_id = ?", (guild_id,))
        row = await cur.fetchone()
        return dict(row)

    async def update_automod(self, guild_id: int, **fields):
        await self.get_automod(guild_id)
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self._conn.execute(f"UPDATE automod_settings SET {cols} WHERE guild_id = ?", values)
        await self._conn.commit()

    @staticmethod
    def automod_whitelisted_channels(settings: dict) -> list[int]:
        return [int(c) for c in (settings.get("whitelist_channel_ids") or "").split(",") if c]

    @staticmethod
    def automod_whitelisted_roles(settings: dict) -> list[int]:
        return [int(r) for r in (settings.get("whitelist_role_ids") or "").split(",") if r]

    async def automod_add_whitelist_channel(self, guild_id: int, channel_id: int):
        s = await self.get_automod(guild_id)
        ids = self.automod_whitelisted_channels(s)
        if channel_id not in ids:
            ids.append(channel_id)
        await self.update_automod(guild_id, whitelist_channel_ids=",".join(str(i) for i in ids))

    async def automod_remove_whitelist_channel(self, guild_id: int, channel_id: int):
        s = await self.get_automod(guild_id)
        ids = [i for i in self.automod_whitelisted_channels(s) if i != channel_id]
        await self.update_automod(guild_id, whitelist_channel_ids=",".join(str(i) for i in ids))

    async def automod_add_whitelist_role(self, guild_id: int, role_id: int):
        s = await self.get_automod(guild_id)
        ids = self.automod_whitelisted_roles(s)
        if role_id not in ids:
            ids.append(role_id)
        await self.update_automod(guild_id, whitelist_role_ids=",".join(str(i) for i in ids))

    async def automod_remove_whitelist_role(self, guild_id: int, role_id: int):
        s = await self.get_automod(guild_id)
        ids = [i for i in self.automod_whitelisted_roles(s) if i != role_id]
        await self.update_automod(guild_id, whitelist_role_ids=",".join(str(i) for i in ids))

    # ---------- VoiceMaster ----------
    async def get_voicemaster(self, guild_id: int) -> dict:
        await self._conn.execute(
            "INSERT OR IGNORE INTO voicemaster_settings (guild_id) VALUES (?)", (guild_id,)
        )
        await self._conn.commit()
        cur = await self._conn.execute("SELECT * FROM voicemaster_settings WHERE guild_id = ?", (guild_id,))
        return dict(await cur.fetchone())

    async def update_voicemaster(self, guild_id: int, **fields):
        await self.get_voicemaster(guild_id)
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self._conn.execute(f"UPDATE voicemaster_settings SET {cols} WHERE guild_id = ?", values)
        await self._conn.commit()

    async def add_voicemaster_channel(self, channel_id: int, guild_id: int, owner_id: int):
        await self._conn.execute(
            "INSERT OR REPLACE INTO voicemaster_channels (channel_id, guild_id, owner_id) VALUES (?, ?, ?)",
            (channel_id, guild_id, owner_id),
        )
        await self._conn.commit()

    async def remove_voicemaster_channel(self, channel_id: int):
        await self._conn.execute("DELETE FROM voicemaster_channels WHERE channel_id = ?", (channel_id,))
        await self._conn.commit()

    async def get_voicemaster_channel(self, channel_id: int) -> dict | None:
        cur = await self._conn.execute(
            "SELECT * FROM voicemaster_channels WHERE channel_id = ?", (channel_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_voicemaster_owner(self, channel_id: int, owner_id: int):
        await self._conn.execute(
            "UPDATE voicemaster_channels SET owner_id = ? WHERE channel_id = ?", (owner_id, channel_id)
        )
        await self._conn.commit()

    async def all_voicemaster_channels(self, guild_id: int) -> list[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM voicemaster_channels WHERE guild_id = ?", (guild_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

    # ---------- Reaction roles ----------
    async def add_reaction_role(self, guild_id: int, message_id: int, channel_id: int, emoji: str, role_id: int):
        await self._conn.execute(
            "INSERT OR REPLACE INTO reaction_roles (guild_id, message_id, channel_id, emoji, role_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, message_id, channel_id, emoji, role_id),
        )
        await self._conn.commit()

    async def get_reaction_role(self, message_id: int, emoji: str) -> dict | None:
        cur = await self._conn.execute(
            "SELECT * FROM reaction_roles WHERE message_id = ? AND emoji = ?", (message_id, emoji)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_reaction_roles(self, guild_id: int) -> list[dict]:
        cur = await self._conn.execute("SELECT * FROM reaction_roles WHERE guild_id = ?", (guild_id,))
        return [dict(r) for r in await cur.fetchall()]

    async def reset_reaction_roles(self, guild_id: int):
        await self._conn.execute("DELETE FROM reaction_roles WHERE guild_id = ?", (guild_id,))
        await self._conn.commit()

    async def remove_reaction_role(self, message_id: int, emoji: str):
        await self._conn.execute(
            "DELETE FROM reaction_roles WHERE message_id = ? AND emoji = ?", (message_id, emoji)
        )
        await self._conn.commit()

    # ---------- Voice join/leave logging ----------
    async def get_voicelog(self, guild_id: int) -> dict:
        await self._conn.execute(
            "INSERT OR IGNORE INTO voicelog_settings (guild_id) VALUES (?)", (guild_id,)
        )
        await self._conn.commit()
        cur = await self._conn.execute("SELECT * FROM voicelog_settings WHERE guild_id = ?", (guild_id,))
        return dict(await cur.fetchone())

    async def update_voicelog(self, guild_id: int, **fields):
        await self.get_voicelog(guild_id)
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self._conn.execute(f"UPDATE voicelog_settings SET {cols} WHERE guild_id = ?", values)
        await self._conn.commit()

    # ---------- VC roles (auto-granted while connected to a given VC) ----------
    async def set_vcrole(self, guild_id: int, channel_id: int, role_id: int):
        await self._conn.execute(
            "INSERT OR REPLACE INTO vcrole_map (guild_id, channel_id, role_id) VALUES (?, ?, ?)",
            (guild_id, channel_id, role_id),
        )
        await self._conn.commit()

    async def remove_vcrole(self, guild_id: int, channel_id: int):
        await self._conn.execute(
            "DELETE FROM vcrole_map WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id)
        )
        await self._conn.commit()

    async def get_vcrole(self, guild_id: int, channel_id: int) -> int | None:
        cur = await self._conn.execute(
            "SELECT role_id FROM vcrole_map WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id)
        )
        row = await cur.fetchone()
        return row["role_id"] if row else None

    async def list_vcroles(self, guild_id: int) -> list[dict]:
        cur = await self._conn.execute("SELECT * FROM vcrole_map WHERE guild_id = ?", (guild_id,))
        return [dict(r) for r in await cur.fetchall()]

    # ---------- Voice moderators ----------
    async def add_vcmod(self, guild_id: int, user_id: int):
        await self._conn.execute("INSERT OR IGNORE INTO vcmods (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
        await self._conn.commit()

    async def remove_vcmod(self, guild_id: int, user_id: int):
        await self._conn.execute("DELETE FROM vcmods WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        await self._conn.commit()

    async def is_vcmod(self, guild_id: int, user_id: int) -> bool:
        cur = await self._conn.execute(
            "SELECT 1 FROM vcmods WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        return (await cur.fetchone()) is not None

    async def list_vcmods(self, guild_id: int) -> list[int]:
        cur = await self._conn.execute("SELECT user_id FROM vcmods WHERE guild_id = ?", (guild_id,))
        return [r["user_id"] for r in await cur.fetchall()]

    # ---------- Voice bans ----------
    async def add_vcban(self, guild_id: int, user_id: int):
        await self._conn.execute("INSERT OR IGNORE INTO vcbans (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
        await self._conn.commit()

    async def remove_vcban(self, guild_id: int, user_id: int):
        await self._conn.execute("DELETE FROM vcbans WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        await self._conn.commit()

    async def is_vcbanned(self, guild_id: int, user_id: int) -> bool:
        cur = await self._conn.execute(
            "SELECT 1 FROM vcbans WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        return (await cur.fetchone()) is not None

    async def list_vcbans(self, guild_id: int) -> list[int]:
        cur = await self._conn.execute("SELECT user_id FROM vcbans WHERE guild_id = ?", (guild_id,))
        return [r["user_id"] for r in await cur.fetchall()]


db = Database()
