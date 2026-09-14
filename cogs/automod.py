"""
Automod: anti-link, anti-spam, anti-token(leak), and anti-caps protection.

- Anti-Link: deletes messages containing URLs / bare domains (e.g. `web.xyz`,
  `foo.com`, Discord invites) and times the sender out.
- Anti-Spam: AUTOMOD_SPAM_MSG_LIMIT or more messages inside
  AUTOMOD_SPAM_WINDOW_SECONDS seconds triggers a mute.
- Anti-Token: catches accidentally (or maliciously) pasted Discord tokens
  and mutes + deletes on sight — this protects the *server*, since a leaked
  token posted in chat is a real compromise risk for whoever owns it.
- Anti-Caps: any single ALL-CAPS word at least `anticaps_min_len` letters
  long (default 5) deletes the message and times the sender out — same as
  the others.

All four run directly off the raw `on_message` event (no command parsing,
no extra round trips, no artificial delay) so the reaction is effectively
instant. Server admins (Administrator / Manage Server) and whitelisted
channels/roles are exempt.
"""
import re
import time
import logging
import datetime
import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import (
    SUCCESS_COLOR,
    ERROR_COLOR,
    EMBED_COLOR,
    LINK_REGEX,
    TOKEN_REGEX,
    AUTOMOD_SPAM_MSG_LIMIT,
    AUTOMOD_SPAM_WINDOW_SECONDS,
    AUTOMOD_DEFAULT_MUTE_SECONDS,
)
from utils.embeds import brand_embed, run_step_sequence

log = logging.getLogger("welcomer")

_LINK_RE = re.compile(LINK_REGEX, re.IGNORECASE)
_TOKEN_RE = re.compile(TOKEN_REGEX)
_WORD_RE = re.compile(r"[A-Za-z]+")


def _has_shouty_word(content: str, min_len: int) -> bool:
    """A word counts as 'shouty' if it's all-alphabetic, at least min_len
    letters, and every letter in it is uppercase."""
    for word in _WORD_RE.findall(content):
        if len(word) >= min_len and word.isupper():
            return True
    return False


class Automod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {(guild_id, user_id): [timestamps...]}
        self._spam_tracker: dict[tuple[int, int], list[float]] = {}

    # ---------- helpers ----------
    def _is_exempt(self, member: discord.Member, settings: dict) -> bool:
        if member.bot:
            return True
        perms = member.guild_permissions
        if perms.administrator or perms.manage_guild:
            return True
        whitelisted_roles = db.automod_whitelisted_roles(settings)
        return any(r.id in whitelisted_roles for r in member.roles)

    async def _punish(self, message: discord.Message, settings: dict, reason: str):
        guild = message.guild
        member = message.author
        mute_seconds = settings.get("mute_seconds") or AUTOMOD_DEFAULT_MUTE_SECONDS

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        timed_out = False
        if guild.me.guild_permissions.moderate_members and member.top_role < guild.me.top_role:
            try:
                await member.timeout(discord.utils.utcnow() + datetime.timedelta(seconds=mute_seconds), reason=reason)
                timed_out = True
            except discord.HTTPException:
                pass

        warn = brand_embed(
            self.bot,
            title="🛡️ Automod",
            description=(
                f"{member.mention}, that message was removed: **{reason}**."
                + (f"\nYou've been muted for `{mute_seconds}s`." if timed_out else "")
            ),
            color=ERROR_COLOR,
        )
        try:
            note = await message.channel.send(embed=warn)
            await note.delete(delay=6)
        except discord.HTTPException:
            pass

        log_channel_id = settings.get("log_channel_id")
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                log_embed = brand_embed(
                    self.bot,
                    title="🛡️ Automod action",
                    description=(
                        f"**User:** {member.mention} (`{member.id}`)\n"
                        f"**Channel:** {message.channel.mention}\n"
                        f"**Reason:** {reason}\n"
                        f"**Timed out:** {'Yes, ' + str(mute_seconds) + 's' if timed_out else 'No'}\n"
                        f"**Message content:** {discord.utils.escape_markdown(message.content)[:900] or '*(empty)*'}"
                    ),
                    color=ERROR_COLOR,
                )
                try:
                    await log_channel.send(embed=log_embed)
                except discord.HTTPException:
                    pass

    # ---------- the actual filter ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        settings = await db.get_automod(message.guild.id)
        if not any([settings["antilink"], settings["antispam"], settings["antitoken"], settings["anticaps"]]):
            return

        member = message.author
        if not isinstance(member, discord.Member):
            return
        if self._is_exempt(member, settings):
            return

        whitelisted_channels = db.automod_whitelisted_channels(settings)
        if message.channel.id in whitelisted_channels:
            return

        # --- anti-token (checked first: highest severity, a real leak) ---
        if settings["antitoken"] and _TOKEN_RE.search(message.content):
            await self._punish(message, settings, "posting what looks like a bot/user token")
            return

        # --- anti-link ---
        if settings["antilink"] and _LINK_RE.search(message.content):
            await self._punish(message, settings, "posting a link")
            return

        # --- anti-caps ---
        if settings["anticaps"] and _has_shouty_word(message.content, settings["anticaps_min_len"]):
            await self._punish(message, settings, f"excessive caps (a word {settings['anticaps_min_len']}+ letters, all uppercase)")
            return

        # --- anti-spam ---
        if settings["antispam"]:
            key = (message.guild.id, member.id)
            now = time.monotonic()
            timestamps = [t for t in self._spam_tracker.get(key, []) if now - t <= AUTOMOD_SPAM_WINDOW_SECONDS]
            timestamps.append(now)
            self._spam_tracker[key] = timestamps
            if len(timestamps) >= AUTOMOD_SPAM_MSG_LIMIT:
                self._spam_tracker[key] = []
                await self._punish(
                    message, settings,
                    f"spamming ({AUTOMOD_SPAM_MSG_LIMIT} or more messages in {AUTOMOD_SPAM_WINDOW_SECONDS}s)",
                )
                return

    # ---------- config commands ----------
    automod_group = app_commands.Group(
        name="automod", description="Configure automod (anti-link / anti-spam / anti-token)",
        default_permissions=discord.Permissions(manage_guild=True),
    )
    whitelist_group = app_commands.Group(
        name="whitelist", description="Exempt a channel or role from automod", parent=automod_group,
    )

    @automod_group.command(name="enable", description="Turn on anti-link, anti-spam & anti-token protection")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def enable(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=brand_embed(self.bot, description="⚡ Booting up...", color=EMBED_COLOR)
        )
        msg = await interaction.original_response()
        steps = [
            "Initializing Automod Core...",
            "Setting up Anti-Spam Protection...",
            "Activating Anti-Link Protection...",
            "Setting up Anti-Token Leak Defense...",
            "Activating Anti-Caps Protection...",
            "Finalizing configuration...",
        ]
        await run_step_sequence(msg, self.bot, "Enabling Automod System", steps, emoji="🛡️")
        await db.update_automod(interaction.guild_id, antilink=1, antispam=1, antitoken=1, anticaps=1)
        final = brand_embed(
            self.bot,
            title="✅ Automod enabled",
            description=(
                "Anti-Link, Anti-Spam, Anti-Token, and Anti-Caps protection are all **active**.\n"
                "Fine-tune with `/automod antilink|antispam|antitoken|anticaps`, `/automod capslength`, "
                "`/automod mutetime`, `/automod logchannel`, and `/automod whitelist channel|role`."
            ),
            color=SUCCESS_COLOR,
        )
        await msg.edit(embed=final)

    @automod_group.command(name="disable", description="Turn off all automod protection")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction):
        await db.update_automod(interaction.guild_id, antilink=0, antispam=0, antitoken=0, anticaps=0)
        await interaction.response.send_message(
            embed=brand_embed(self.bot, description="🛑 Automod fully disabled.", color=SUCCESS_COLOR)
        )

    @automod_group.command(name="antilink", description="Toggle anti-link protection")
    @app_commands.describe(state="on or off")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def antilink(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        await db.update_automod(interaction.guild_id, antilink=1 if state.value == "on" else 0)
        await interaction.response.send_message(
            embed=brand_embed(self.bot, description=f"🔗 Anti-Link is now **{state.value}**.", color=SUCCESS_COLOR)
        )

    @automod_group.command(name="antispam", description="Toggle anti-spam protection")
    @app_commands.describe(state="on or off")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def antispam(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        await db.update_automod(interaction.guild_id, antispam=1 if state.value == "on" else 0)
        await interaction.response.send_message(
            embed=brand_embed(self.bot, description=f"⏱️ Anti-Spam is now **{state.value}**.", color=SUCCESS_COLOR)
        )

    @automod_group.command(name="antitoken", description="Toggle anti-token (leaked token) protection")
    @app_commands.describe(state="on or off")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def antitoken(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        await db.update_automod(interaction.guild_id, antitoken=1 if state.value == "on" else 0)
        await interaction.response.send_message(
            embed=brand_embed(self.bot, description=f"🔑 Anti-Token is now **{state.value}**.", color=SUCCESS_COLOR)
        )

    @automod_group.command(name="anticaps", description="Toggle anti-caps (excessive CAPS) protection")
    @app_commands.describe(state="on or off")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def anticaps(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        await db.update_automod(interaction.guild_id, anticaps=1 if state.value == "on" else 0)
        await interaction.response.send_message(
            embed=brand_embed(self.bot, description=f"🔠 Anti-Caps is now **{state.value}**.", color=SUCCESS_COLOR)
        )

    @automod_group.command(name="capslength", description="Set the minimum length of an ALL-CAPS word that triggers anti-caps")
    @app_commands.describe(length="A word this many letters or longer, fully uppercase, triggers it (default 5)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def capslength(self, interaction: discord.Interaction, length: app_commands.Range[int, 2, 50]):
        await db.update_automod(interaction.guild_id, anticaps_min_len=length)
        await interaction.response.send_message(
            embed=brand_embed(self.bot, description=f"🔠 Anti-Caps now triggers on ALL-CAPS words of `{length}+` letters.", color=SUCCESS_COLOR)
        )

    @automod_group.command(name="mutetime", description="Set how long automod violations get timed out for")
    @app_commands.describe(seconds="Timeout duration in seconds (max 2419200 = 28 days)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def mutetime(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 5, 2419200]):
        await db.update_automod(interaction.guild_id, mute_seconds=seconds)
        await interaction.response.send_message(
            embed=brand_embed(self.bot, description=f"⏲️ Automod timeout duration set to `{seconds}s`.", color=SUCCESS_COLOR)
        )

    @automod_group.command(name="logchannel", description="Set the channel automod actions are logged to")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def logchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update_automod(interaction.guild_id, log_channel_id=channel.id)
        await interaction.response.send_message(
            embed=brand_embed(self.bot, description=f"📝 Automod actions will be logged in {channel.mention}.", color=SUCCESS_COLOR)
        )

    @automod_group.command(name="status", description="Show the current automod configuration")
    async def status(self, interaction: discord.Interaction):
        s = await db.get_automod(interaction.guild_id)
        def flag(v):
            return "✅ On" if v else "❌ Off"
        log_ch = interaction.guild.get_channel(s["log_channel_id"]) if s["log_channel_id"] else None
        wl_channels = db.automod_whitelisted_channels(s)
        wl_roles = db.automod_whitelisted_roles(s)
        embed = brand_embed(
            self.bot,
            title="🛡️ Automod status",
            description=(
                f"**Anti-Link:** {flag(s['antilink'])}\n"
                f"**Anti-Spam:** {flag(s['antispam'])}\n"
                f"**Anti-Token:** {flag(s['antitoken'])}\n"
                f"**Anti-Caps:** {flag(s['anticaps'])} (triggers at `{s['anticaps_min_len']}+` letter ALL-CAPS words)\n"
                f"**Timeout duration:** `{s['mute_seconds']}s`\n"
                f"**Log channel:** {log_ch.mention if log_ch else '*not set*'}\n"
                f"**Whitelisted channels:** {len(wl_channels)}\n"
                f"**Whitelisted roles:** {len(wl_roles)}"
            ),
            color=EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @whitelist_group.command(name="channel", description="Add or remove a channel from the automod whitelist")
    @app_commands.describe(action="add or remove", channel="the channel")
    @app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def whitelist_channel(self, interaction: discord.Interaction, action: app_commands.Choice[str], channel: discord.TextChannel):
        if action.value == "add":
            await db.automod_add_whitelist_channel(interaction.guild_id, channel.id)
            desc = f"✅ {channel.mention} is now exempt from automod."
        else:
            await db.automod_remove_whitelist_channel(interaction.guild_id, channel.id)
            desc = f"✅ {channel.mention} is no longer exempt from automod."
        await interaction.response.send_message(embed=brand_embed(self.bot, description=desc, color=SUCCESS_COLOR))

    @whitelist_group.command(name="role", description="Add or remove a role from the automod whitelist")
    @app_commands.describe(action="add or remove", role="the role")
    @app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def whitelist_role(self, interaction: discord.Interaction, action: app_commands.Choice[str], role: discord.Role):
        if action.value == "add":
            await db.automod_add_whitelist_role(interaction.guild_id, role.id)
            desc = f"✅ {role.mention} is now exempt from automod."
        else:
            await db.automod_remove_whitelist_role(interaction.guild_id, role.id)
            desc = f"✅ {role.mention} is no longer exempt from automod."
        await interaction.response.send_message(embed=brand_embed(self.bot, description=desc, color=SUCCESS_COLOR))

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            embed = brand_embed(self.bot, title="❌ Missing permission", description="You need **Manage Server** permission to use this.", color=ERROR_COLOR)
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            log.exception("Automod command error", exc_info=error)
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Automod(bot))
