"""
Central config & constants for the welcomer bot.
"""
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# By default discord.py figures out the bot owner automatically by asking
# Discord who owns this application (or, for team-owned apps, treats every
# team member as an owner). Setting OWNER_IDS here overrides that lookup
# entirely — only the ID(s) listed will pass bot.is_owner(), regardless of
# who actually owns the application in the Developer Portal. Comma-separate
# multiple IDs. Leave empty to keep the automatic Developer Portal behavior.
_owner_ids_raw = os.getenv("OWNER_IDS", "").strip()
OWNER_IDS = {int(i) for i in _owner_ids_raw.split(",") if i.strip().isdigit()} or None

DB_PATH = os.getenv("DB_PATH", "welcomer.db")

# ---- Lavalink (music) ----
# Point this at any Lavalink v4 node — self-hosted or a trusted public one.
# The bot itself never touches yt-dlp/ffmpeg; the Lavalink server does all
# searching/decoding and streams audio straight into the voice channel.
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "127.0.0.1")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
LAVALINK_SECURE = os.getenv("LAVALINK_SECURE", "false").lower() in ("1", "true", "yes")

# Shown as a button in /help and @Bot help — leave empty to hide it.
SUPPORT_SERVER_URL = os.getenv("SUPPORT_SERVER_URL", "")

# Optional wide banner image shown at the bottom of the help menu (like a
# wordmark/logo). Leave empty to just show the bot's avatar as a thumbnail instead.
HELP_BANNER_URL = os.getenv("HELP_BANNER_URL", "")

# Scoped invite (View/Send/Embed/Attach/History/Manage Channels/Manage Roles/Connect/Speak) —
# not Administrator. See earlier discussion: permissions=8 is full admin and shouldn't be used.
INVITE_PERMISSIONS = 268553232

EMBED_COLOR = 0x2B2D31          # neutral discord dark
SUCCESS_COLOR = 0x57F287
ERROR_COLOR = 0xED4245
INFO_COLOR = 0x5865F2

DEFAULT_WELCOME_MESSAGE = (
    "👋 Welcome {mention} to **{server}**!\n"
    "You are member **#{membercount}**."
)
DEFAULT_LEAVE_MESSAGE = (
    "👋 **{user}** has left **{server}**.\n"
    "We now have **{membercount}** members."
)
DEFAULT_DM_MESSAGE = (
    "Hey {user_name}, thanks for joining **{server}**! "
    "Make sure to check the rules channel. 🎉"
)

# Placeholders documented for users via /help-placeholders
PLACEHOLDERS_HELP = (
    "`{mention}` - pings the user\n"
    "`{user}` - user's name#tag\n"
    "`{user_name}` - user's display name\n"
    "`{server}` - server name\n"
    "`{membercount}` - current member count\n"
    "`{membercount_ordinal}` - e.g. 42nd\n"
)

# ---- /autosetup polished defaults (nicer than the bare-bones fallback above) ----
AUTOSETUP_WELCOME_MESSAGE = (
    "✨ **Welcome to {server}, {user_name}!** ✨\n"
    "{mention}, you're member **#{membercount}** — glad to have you here.\n"
    "Take a moment to check out the rules and introduce yourself!"
)
AUTOSETUP_LEAVE_MESSAGE = (
    "📤 **{user}** just left **{server}**.\n"
    "We're down to **{membercount}** members. Take care! 👋"
)
AUTOSETUP_DM_MESSAGE = (
    "Hey {user_name}! 👋\n\n"
    "Thanks for joining **{server}** — we're happy to have you.\n"
    "Be sure to check the rules and introduce yourself when you get a chance.\n\n"
    "See you around! 🎉"
)

WELCOMER_CATEGORY_NAME = "Welcomer"
WELCOME_CHANNEL_NAME = "welcome"
LEAVE_CHANNEL_NAME = "leave-log"
