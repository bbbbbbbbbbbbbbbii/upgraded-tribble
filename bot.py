"""
Professional Discord Welcomer Bot
----------------------------------
Entry point: loads all cogs, connects the database, syncs slash commands,
and runs the bot.

Setup:
    1. pip install -r requirements.txt
    2. Copy .env.example to .env and add your bot token
    3. python bot.py

Required bot intents (enable in Discord Developer Portal → Bot):
    - SERVER MEMBERS INTENT (required for join/leave events & autorole)
"""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands
import wavelink

from config import (
    TOKEN,
    DEV_GUILD_ID,
    ERROR_COLOR,
    LAVALINK_HOST,
    LAVALINK_PORT,
    LAVALINK_PASSWORD,
    LAVALINK_SECURE,
)
from database import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("welcomer")

INTENTS = discord.Intents.default()
INTENTS.members = True  # required for on_member_join / on_member_remove / autorole
INTENTS.message_content = True  # required to read "@Bot ping" style mention-commands
INTENTS.voice_states = True  # required for the music cog (join/play/24-7)

EXTENSIONS = [
    "cogs.welcome",
    "cogs.leave",
    "cogs.autorole",
    "cogs.autosetup",
    "cogs.general",
    "cogs.mention_commands",
    "cogs.music",
]


class WelcomerBot(commands.Bot):
    def __init__(self):
        # Mention-prefix (e.g. "@Welcomer ping") always works; "!wb " still works too.
        super().__init__(command_prefix=commands.when_mentioned_or("!wb "), intents=INTENTS, help_command=None)

    async def setup_hook(self):
        await db.connect()

        # Connect to the Lavalink node used for all music playback. If the
        # node isn't up yet, don't crash the whole bot — the music cog checks
        # `wavelink.Pool.nodes` before doing anything and replies with a clear
        # "music isn't available right now" message instead.
        scheme = "https" if LAVALINK_SECURE else "http"
        node = wavelink.Node(
            uri=f"{scheme}://{LAVALINK_HOST}:{LAVALINK_PORT}",
            password=LAVALINK_PASSWORD,
        )
        try:
            await wavelink.Pool.connect(nodes=[node], client=self)
        except Exception:
            log.exception(
                "Could not connect to the Lavalink node at %s:%s — music commands "
                "won't work until it's reachable. See README for Lavalink setup.",
                LAVALINK_HOST, LAVALINK_PORT,
            )

        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info(f"Loaded extension: {ext}")

        if DEV_GUILD_ID:
            guild = discord.Object(id=int(DEV_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info(f"Synced {len(synced)} commands to dev guild {DEV_GUILD_ID}")
        else:
            synced = await self.tree.sync()
            log.info(f"Synced {len(synced)} global commands")

    async def on_ready(self):
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="new members join 👋")
        )

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        ctx = await self.get_context(message)
        if ctx.valid:
            # A real "@Bot <command>" or "!wb <command>" — run it.
            await self.invoke(ctx)
            return

        # Mentioned, but not a recognized command — give a friendly nudge instead
        # of silently ignoring them.
        is_direct_mention = self.user in message.mentions and not message.mention_everyone
        if is_direct_mention:
            embed = discord.Embed(
                title="👋 Hey there!",
                description=(
                    f"I'm **{self.user.name}**! Try mentioning me with a command, like:\n"
                    f"`@{self.user.name} help` · `@{self.user.name} ping` · `@{self.user.name} play <song>`\n\n"
                    "Or use my slash commands — type `/` and pick one. ✨"
                ),
                color=discord.Color.blurple(),
            )
            embed.set_footer(
                text="Tip: /autosetup gets welcome, leave & DM messages configured in one step 🚀",
                icon_url=self.user.display_avatar.url,
            )
            try:
                await message.reply(embed=embed, mention_author=False)
            except discord.HTTPException:
                pass

    async def close(self):
        await db.close()
        await super().close()


bot = WelcomerBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ You need **Manage Server** permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = f"❌ I'm missing permissions to do that: {', '.join(error.missing_permissions)}"
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = f"⏳ Slow down — try again in {error.retry_after:.1f}s."
    else:
        log.exception("Unhandled app command error", exc_info=error)
        msg = "❌ Something went wrong running that command."

    embed = discord.Embed(description=msg, color=ERROR_COLOR)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Handles errors from mention-prefix commands like '@Bot play ...'."""
    if isinstance(error, commands.CommandNotFound):
        return  # already handled by the friendly nudge in on_message
    elif isinstance(error, commands.MissingPermissions):
        msg = "❌ You need **Manage Server** permission to use this command."
    elif isinstance(error, commands.MissingRequiredArgument):
        msg = f"❌ Missing something — usage: `{ctx.prefix}{ctx.command} {ctx.command.signature}`"
    elif isinstance(error, commands.BadArgument):
        msg = "❌ I couldn't understand one of those arguments."
    else:
        log.exception("Unhandled command error", exc_info=error)
        msg = "❌ Something went wrong running that command."
    embed = discord.Embed(description=msg, color=ERROR_COLOR)
    try:
        await ctx.reply(embed=embed, mention_author=False)
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token.")
    asyncio.run(bot.start(TOKEN))
