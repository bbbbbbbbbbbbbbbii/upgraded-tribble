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

    embed_color         INTEGER,
    total_joins         INTEGER NOT NULL DEFAULT 0,
    total_leaves        INTEGER NOT NULL DEFAULT 0
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
        await self._conn.execute(SCHEMA)
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


db = Database()
