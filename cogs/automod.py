"""
Automod: the "mute for spam-tier stuff" layer, distinct from anti-nuke's
"ban for destructive-tier stuff". If you want the exact split:

  - Automod (this file) mutes people for spammy-but-not-destructive
    behavior: pinging several individual members at once, posting an IP
    address, saying a blacklisted word, spamming the same message.
  - Anti-nuke (cogs/antinuke.py) bans people for anything that could
    actually damage the server: @everyone/@here pings, mass role pings,
    deleting/creating channels or roles, banning/kicking people, etc.

These two deliberately don't overlap on the same trigger with different
punishments — "ping 3 people" always mutes (automod), "ping @everyone"
always bans (antinuke) — so there's no contradiction between them.

Timing: same note as antinuke.py — reactions happen the instant the
message is seen, no polling delay, but a Discord API call is still a real
network round-trip (~100-300ms), not a literal fraction of a millisecond.
"""
import re
import time
import logging
import datetime
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import SUCCESS_COLOR, ERROR_COLOR
from utils.embeds import brand_embed

log = logging.getLogger("welcomer.automod")

IP_REGEX = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")


class Automod(commands.Cog, name="Automod"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # per (guild_id, user_id): deque of (timestamp, content) for anti-duplicate
        self._recent_messages: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=10))

    async def _timeout(self, member: discord.Member, seconds: int, reason: str) -> bool:
        try:
            await member.timeout(discord.utils.utcnow() + datetime.timedelta(seconds=seconds), reason=reason)
            return True
        except Exception:
            return False

    async def _log(self, guild: discord.Guild, title: str, description: str, color=ERROR_COLOR):
        settings = await db.get_automod(guild.id)
        channel = guild.get_channel(settings["log_channel_id"]) if settings["log_channel_id"] else None
        if channel:
            try:
                await channel.send(embed=brand_embed(self.bot, title=title, description=description, color=color))
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or not isinstance(message.author, discord.Member):
            return
        if message.author.guild_permissions.administrator:
            return  # never automod admins

        guild = message.guild
        settings = await db.get_automod(guild.id)
        author = message.author

        # ---------- mass mention (individual users) ----------
        distinct_mentions = {u.id for u in message.mentions if not u.bot}
        if len(distinct_mentions) > settings["mass_mention_limit"] and not message.mention_everyone:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            if await self._timeout(author, settings["mute_seconds"], "Automod: mass mention"):
                await self._log(guild, "🔇 Mass Mention", f"{author.mention} pinged {len(distinct_mentions)} members in one message — muted {settings['mute_seconds']}s.")
            return

        # ---------- IP address ----------
        if settings["ip_filter_enabled"] and IP_REGEX.search(message.content):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            if await self._timeout(author, settings["mute_seconds"], "Automod: posted an IP address"):
                await self._log(guild, "🌐 IP Address Blocked", f"{author.mention} tried to post an IP address — muted {settings['mute_seconds']}s.")
            return

        # ---------- blacklisted words ----------
        words = await db.list_blacklist_words(guild.id)
        if words:
            lowered = message.content.lower()
            hit = next((w for w in words if w in lowered), None)
            if hit:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await db.add_warning(guild.id, author.id, self.bot.user.id, f"Blacklisted word: {hit}")
                if await self._timeout(author, settings["mute_seconds"], f"Automod: blacklisted word ({hit})"):
                    await self._log(guild, "🚫 Blacklisted Word", f"{author.mention} used a blacklisted word — warned, message deleted, muted {settings['mute_seconds']}s.")
                return

        # ---------- anti-duplicate ----------
        key = (guild.id, author.id)
        now = time.monotonic()
        history = self._recent_messages[key]
        history.append((now, message.content))
        window = settings["duplicate_window"]
        recent_same = [c for t, c in history if now - t <= window and c == message.content and c.strip()]
        if len(recent_same) >= settings["duplicate_limit"]:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            await db.add_warning(guild.id, author.id, self.bot.user.id, "Repeated the same message (anti-duplicate)")
            if await self._timeout(author, settings["mute_seconds"], "Automod: anti-duplicate spam"):
                await self._log(guild, "📋 Duplicate Spam", f"{author.mention} repeated the same message {len(recent_same)}x — warned, muted {settings['mute_seconds']}s.")
            history.clear()

    # ---------- config commands ----------
    @commands.group(name="automod", invoke_without_command=True)
    async def automod_group(self, ctx: commands.Context):
        settings = await db.get_automod(ctx.guild.id)
        words = await db.list_blacklist_words(ctx.guild.id)
        log_channel_line = f"<#{settings['log_channel_id']}>" if settings["log_channel_id"] else "not set"
        embed = brand_embed(
            self.bot, title="🛡️ Automod Settings",
            description=(
                f"**Mass mention limit:** {settings['mass_mention_limit']} users → mute {settings['mute_seconds']}s\n"
                f"**IP address filter:** {'✅ on' if settings['ip_filter_enabled'] else '❌ off'}\n"
                f"**Anti-duplicate:** {settings['duplicate_limit']} repeats within {settings['duplicate_window']}s\n"
                f"**Blacklisted words:** {len(words)} word(s)\n"
                f"**Log channel:** {log_channel_line}"
            ),
        )
        await ctx.reply(embed=embed, mention_author=False)

    @automod_group.command(name="logs")
    @commands.has_permissions(administrator=True)
    async def automod_logs(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.update_automod(ctx.guild.id, log_channel_id=channel.id)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Automod logs will be sent to {channel.mention}.", color=SUCCESS_COLOR), mention_author=False)

    @automod_group.command(name="muteduration")
    @commands.has_permissions(administrator=True)
    async def automod_mute_duration(self, ctx: commands.Context, seconds: int):
        await db.update_automod(ctx.guild.id, mute_seconds=max(30, min(seconds, 2419200)))
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Automod mute duration set to {seconds}s.", color=SUCCESS_COLOR), mention_author=False)

    @automod_group.command(name="ipfilter")
    @commands.has_permissions(administrator=True)
    async def automod_ip_filter(self, ctx: commands.Context, state: str):
        await db.update_automod(ctx.guild.id, ip_filter_enabled=1 if state.lower() in ("on", "true", "enable") else 0)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ IP filter: **{state}**.", color=SUCCESS_COLOR), mention_author=False)

    # ---------- blacklist words ----------
    @commands.group(name="blacklist", invoke_without_command=True)
    async def blacklist_group(self, ctx: commands.Context):
        words = await db.list_blacklist_words(ctx.guild.id)
        desc = ", ".join(f"`{w}`" for w in words) if words else "No blacklisted words yet."
        await ctx.reply(embed=brand_embed(self.bot, title="🚫 Blacklisted Words", description=desc), mention_author=False)

    @blacklist_group.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def blacklist_add(self, ctx: commands.Context, *, word: str):
        await db.add_blacklist_word(ctx.guild.id, word)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Added `{word}` to the blacklist.", color=SUCCESS_COLOR), mention_author=False)

    @blacklist_group.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def blacklist_remove(self, ctx: commands.Context, *, word: str):
        await db.remove_blacklist_word(ctx.guild.id, word)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Removed `{word}` from the blacklist.", color=SUCCESS_COLOR), mention_author=False)

    # slash mirror, as explicitly requested ("/add blacklist")
    @app_commands.command(name="addblacklist", description="Add a word to this server's automod blacklist")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def slash_addblacklist(self, interaction: discord.Interaction, word: str):
        await db.add_blacklist_word(interaction.guild_id, word)
        await interaction.response.send_message(embed=brand_embed(self.bot, description=f"✅ Added `{word}` to the blacklist.", color=SUCCESS_COLOR), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Automod(bot))
