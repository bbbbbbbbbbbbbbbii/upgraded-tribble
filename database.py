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


db = Database()
