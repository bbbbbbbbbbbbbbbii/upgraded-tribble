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

from config import TOKEN, DEV_GUILD_ID, ERROR_COLOR
from database import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("welcomer")

INTENTS = discord.Intents.default()
INTENTS.members = True  # required for on_member_join / on_member_remove / autorole

EXTENSIONS = [
    "cogs.welcome",
    "cogs.leave",
    "cogs.autorole",
    "cogs.autosetup",
    "cogs.general",
]


class WelcomerBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!wb ", intents=INTENTS, help_command=None)

    async def setup_hook(self):
        await db.connect()
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
        # Always let prefix commands (e.g. "!wb ...") still work.
        await self.process_commands(message)

        if message.author.bot or message.guild is None:
            return

        # Respond whenever the bot is directly @mentioned (not part of a reply-ping,
        # not @everyone/@here, and not just incidentally in a mention list).
        is_direct_mention = self.user in message.mentions and not message.mention_everyone
        if is_direct_mention:
            content_without_mention = message.content
            for mention_format in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
                content_without_mention = content_without_mention.replace(mention_format, "").strip()

            embed = discord.Embed(
                title="👋 Hey there!",
                description=(
                    f"I'm **{self.user.name}** — I only use **slash commands** now.\n"
                    "Type `/` and pick one of my commands, or run `/help` to see everything I can do. ✨"
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


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token.")
    asyncio.run(bot.start(TOKEN))
