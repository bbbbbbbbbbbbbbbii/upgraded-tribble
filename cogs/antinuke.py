"""
Anti-Nuke security system.

Design notes (read before changing thresholds):
  - "Instant" here means "reacts the moment the audit log entry appears" —
    typically well under a second of *bot-side* processing, but a single
    Discord API round-trip is itself ~100-300ms, so literal sub-millisecond
    timing isn't physically possible over HTTPS. This is as fast as any
    real anti-nuke bot gets: no polling loop, no manual review step.
  - Punishment is always: ban the offending user (Administrator-level bots
    can always out-rank a compromised mod role or a rogue bot, which is
    exactly why this bot needs Administrator to work reliably).
  - Every destructive action this catches also gets *reverted* where
    Discord's API makes that possible (recreate a deleted channel/role,
    unban a banned member, kick a maliciously-added bot). Some things
    can't be undone by any bot (a member who was kicked already left; a
    pruned member's identity isn't in the audit log) — those still ban
    the offender and log it, they just can't un-happen the damage.
  - The owner, this bot itself, and anyone on the whitelist are always
    exempt. Whitelist entries can bypass everything ("all") or just
    specific protections.
"""
import asyncio
import json
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import db
from config import SUCCESS_COLOR, ERROR_COLOR, EMBED_COLOR
from utils.embeds import brand_embed

log = logging.getLogger("welcomer.antinuke")

DANGEROUS_PERMS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "ban_members", "kick_members")

# key -> display label (order matches the setup animation & whitelist config screen)
PROTECTIONS: dict[str, str] = {
    "ban": "Anti Ban",
    "unban": "Anti Unban",
    "kick": "Anti Kick",
    "member_prune": "Anti Member Prune",
    "bot_add": "Anti Bot Add",
    "channel_create": "Anti Channel Create",
    "channel_delete": "Anti Channel Delete",
    "channel_update": "Anti Channel Update",
    "role_create": "Anti Role Create",
    "role_delete": "Anti Role Delete",
    "role_update": "Anti Role Update",
    "member_update": "Anti Member Update",
    "emoji_create": "Anti Emoji/Sticker Create",
    "emoji_delete": "Anti Emoji/Sticker Delete",
    "emoji_update": "Anti Emoji/Sticker Update",
    "everyone_ping": "Anti Everyone/Here Ping",
    "role_ping": "Anti Role Ping",
    "integration": "Anti Integration",
    "guild_update": "Anti Guild Update",
    "webhook_create": "Anti Webhook Create",
    "webhook_delete": "Anti Webhook Delete",
    "webhook_update": "Anti Webhook Update",
}
ALL_KEYS = list(PROTECTIONS.keys())

# Animation steps: (label, [protection keys this step covers])
ENABLE_STEPS = [
    ("Initializing Anti-Nuke Core...", []),
    ("Setting up Ban/Kick Protection...", ["ban", "unban", "kick"]),
    ("Configuring Role Management Security...", ["role_create", "role_delete", "role_update"]),
    ("Enabling Channel Protection...", ["channel_create", "channel_delete", "channel_update"]),
    ("Activating Webhook Security...", ["webhook_create", "webhook_delete", "webhook_update"]),
    ("Setting up Bot Add Protection...", ["bot_add"]),
    ("Configuring Server Settings Guard...", ["guild_update"]),
    ("Setting up Emoji/Sticker Security...", ["emoji_create", "emoji_delete", "emoji_update"]),
    ("Activating Member Prune Protection...", ["member_prune"]),
    ("Configuring Integration Protection...", ["integration"]),
    ("Enabling Everyone/Here Mention Protection...", ["everyone_ping", "role_ping"]),
]


def _channel_snapshot(guild: discord.Guild) -> list[dict]:
    out = []
    for ch in guild.channels:
        out.append({
            "id": ch.id, "name": ch.name, "type": str(ch.type),
            "position": ch.position, "category_id": ch.category_id,
            "topic": getattr(ch, "topic", None),
        })
    return out


def _role_snapshot(guild: discord.Guild) -> list[dict]:
    out = []
    for r in guild.roles:
        out.append({
            "id": r.id, "name": r.name, "color": r.color.value,
            "permissions": r.permissions.value, "position": r.position,
            "hoist": r.hoist, "mentionable": r.mentionable,
        })
    return out


class WallSetupView(discord.ui.View):
    def __init__(self, cog: "Antinuke", guild_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="Setup Wall", style=discord.ButtonStyle.success, emoji="✅")
    async def setup_wall(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await self.cog.build_wall_role(interaction)
        self.stop()

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.danger, emoji="✖️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        embed = brand_embed(self.cog.bot, description="Skipped Wall role setup. You can run `wall setup` later.", color=EMBED_COLOR)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class Antinuke(commands.Cog, name="Antinuke"):
    """Real-time server-nuke protection: instant ban + auto-revert."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._recent_actions: dict[tuple[int, str], float] = {}  # de-dupe rapid duplicate audit entries
        self.snapshot_loop.start()

    def cog_unload(self):
        self.snapshot_loop.cancel()

    # ---------- background snapshot refresh (safety net alongside event-driven updates) ----------
    @tasks.loop(minutes=10)
    async def snapshot_loop(self):
        for guild in self.bot.guilds:
            settings = await db.get_antinuke(guild.id)
            if settings["enabled"]:
                await self._refresh_snapshot(guild)

    @snapshot_loop.before_loop
    async def _before_snapshot_loop(self):
        await self.bot.wait_until_ready()

    async def _refresh_snapshot(self, guild: discord.Guild):
        try:
            await db.save_snapshot(guild.id, json.dumps(_channel_snapshot(guild)), json.dumps(_role_snapshot(guild)))
        except Exception:
            log.exception("Snapshot refresh failed for guild %s", guild.id)

    # ---------- core violation handler ----------
    def _debounced(self, guild_id: int, key: str) -> bool:
        """True if we've already handled this exact (guild, audit-entry) combo very recently —
        audit log events can occasionally be observed by more than one listener path."""
        now = time.monotonic()
        last = self._recent_actions.get((guild_id, key))
        self._recent_actions[(guild_id, key)] = now
        return last is not None and (now - last) < 2.0

    async def is_exempt(self, guild: discord.Guild, user: discord.abc.User | None, protection: str) -> bool:
        if user is None:
            return True
        if user.id == self.bot.user.id or user.id == guild.owner_id:
            return True
        if await self.bot.is_owner(user):
            return True
        if await db.is_whitelisted(guild.id, user.id, protection):
            return True
        return False

    async def protection_active(self, guild_id: int, key: str) -> tuple[bool, dict]:
        settings = await db.get_antinuke(guild_id)
        return bool(settings["enabled"] and key in db.protection_list(settings)), settings

    async def _get_executor(self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int | None = None):
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                if target_id is None or (entry.target and getattr(entry.target, "id", None) == target_id):
                    # ignore stale entries (>10s old) so we don't re-punish for old history on startup
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 10:
                        return entry.user, entry
        except discord.Forbidden:
            log.warning("Missing View Audit Log permission in guild %s", guild.id)
        return None, None

    async def punish(self, guild: discord.Guild, executor: discord.abc.User, protection: str, detail: str):
        """Instant ban, falling back to kick/role-strip if ban is somehow refused."""
        try:
            member = guild.get_member(executor.id) or executor
            await guild.ban(member, reason=f"Anti-Nuke: {PROTECTIONS.get(protection, protection)} — {detail}", delete_message_seconds=0)
            action_taken = "banned"
        except discord.Forbidden:
            try:
                member = guild.get_member(executor.id)
                if member:
                    await member.kick(reason=f"Anti-Nuke (couldn't ban): {detail}")
                    action_taken = "kicked (ban was refused by Discord — check my role position)"
                else:
                    action_taken = "COULD NOT PUNISH — missing permissions"
            except Exception:
                action_taken = "COULD NOT PUNISH — missing permissions"
        except Exception as e:
            action_taken = f"COULD NOT PUNISH — {e}"
        await self.log_action(guild, protection, executor, detail, action_taken)
        return action_taken

    async def log_action(self, guild: discord.Guild, protection: str, executor, detail: str, action_taken: str):
        settings = await db.get_antinuke(guild.id)
        channel = guild.get_channel(settings["log_channel_id"]) if settings["log_channel_id"] else None
        if not channel:
            return
        embed = brand_embed(
            self.bot,
            title="🛡️ Anti-Nuke Triggered",
            description=(
                f"**Protection:** {PROTECTIONS.get(protection, protection)}\n"
                f"**User:** {getattr(executor, 'mention', executor)} (`{getattr(executor, 'id', '?')}`)\n"
                f"**Detail:** {detail}\n"
                f"**Action taken:** {action_taken}"
            ),
            color=ERROR_COLOR,
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ================= EVENT LISTENERS =================

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        active, _ = await self.protection_active(guild.id, "ban")
        if not active:
            return
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.ban, user.id)
        if await self.is_exempt(guild, executor, "ban"):
            return
        if self._debounced(guild.id, f"ban:{user.id}"):
            return
        try:
            await guild.unban(user, reason="Anti-Nuke recovery: reversing unauthorized ban")
            revert_note = f"Unbanned {user}."
        except Exception:
            revert_note = "Could not auto-unban (check my permissions)."
        await self.punish(guild, executor, "ban", f"Banned {user} — {revert_note}")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        active, _ = await self.protection_active(guild.id, "unban")
        if not active:
            return
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.unban, user.id)
        if await self.is_exempt(guild, executor, "unban"):
            return
        if self._debounced(guild.id, f"unban:{user.id}"):
            return
        try:
            await guild.ban(user, reason="Anti-Nuke recovery: reversing unauthorized unban")
            revert_note = f"Re-banned {user}."
        except Exception:
            revert_note = "Could not auto-re-ban."
        await self.punish(guild, executor, "unban", f"Unbanned {user} — {revert_note}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        active, _ = await self.protection_active(guild.id, "kick")
        if not active:
            return
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.kick, member.id)
        if executor is None:
            return  # ordinary leave, not a kick
        if await self.is_exempt(guild, executor, "kick"):
            return
        if self._debounced(guild.id, f"kick:{member.id}"):
            return
        await self.punish(guild, executor, "kick", f"Kicked {member} (can't force-return a kicked member — they must rejoin).")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        active, _ = await self.protection_active(guild.id, "channel_create")
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.channel_create, channel.id)
        if not active or await self.is_exempt(guild, executor, "channel_create"):
            await self._refresh_snapshot(guild)
            return
        if self._debounced(guild.id, f"chcreate:{channel.id}"):
            return
        try:
            await channel.delete(reason="Anti-Nuke recovery: unauthorized channel creation")
            revert_note = f"Deleted the channel it created (#{channel.name})."
        except Exception:
            revert_note = "Could not delete the new channel."
        await self.punish(guild, executor, "channel_create", revert_note)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        active, _ = await self.protection_active(guild.id, "channel_delete")
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.channel_delete, channel.id)
        if not active or await self.is_exempt(guild, executor, "channel_delete"):
            return
        if self._debounced(guild.id, f"chdelete:{channel.id}"):
            return
        revert_note = "Could not recreate the channel."
        try:
            category = guild.get_channel(channel.category_id) if channel.category_id else None
            new_ch = await guild.create_text_channel(
                channel.name, category=category, position=channel.position,
                topic=getattr(channel, "topic", None), reason="Anti-Nuke recovery: restoring deleted channel",
            )
            revert_note = f"Recreated #{new_ch.name} (permission overwrites may need manual review)."
        except Exception:
            pass
        await self.punish(guild, executor, "channel_delete", f"Deleted #{channel.name} — {revert_note}")

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        guild = after.guild
        active, _ = await self.protection_active(guild.id, "channel_update")
        if not active:
            return
        if before.name == after.name and getattr(before, "topic", None) == getattr(after, "topic", None) and before.position == after.position:
            return
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.channel_update, after.id)
        if await self.is_exempt(guild, executor, "channel_update"):
            await self._refresh_snapshot(guild)
            return
        if self._debounced(guild.id, f"chupdate:{after.id}"):
            return
        try:
            kwargs = {"name": before.name, "reason": "Anti-Nuke recovery: reverting channel edit"}
            if hasattr(before, "topic"):
                kwargs["topic"] = before.topic
            await after.edit(**kwargs)
            revert_note = f"Reverted #{after.name} back to its previous name/topic."
        except Exception:
            revert_note = "Could not revert the channel edit."
        await self.punish(guild, executor, "channel_update", revert_note)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        guild = role.guild
        active, _ = await self.protection_active(guild.id, "role_create")
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.role_create, role.id)
        if not active or await self.is_exempt(guild, executor, "role_create"):
            await self._refresh_snapshot(guild)
            return
        if self._debounced(guild.id, f"rolecreate:{role.id}"):
            return
        try:
            await role.delete(reason="Anti-Nuke recovery: unauthorized role creation")
            revert_note = f"Deleted the role it created ({role.name})."
        except Exception:
            revert_note = "Could not delete the new role."
        await self.punish(guild, executor, "role_create", revert_note)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild = role.guild
        active, _ = await self.protection_active(guild.id, "role_delete")
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.role_delete, role.id)
        if not active or await self.is_exempt(guild, executor, "role_delete"):
            return
        if self._debounced(guild.id, f"roledelete:{role.id}"):
            return
        revert_note = "Could not recreate the role."
        try:
            new_role = await guild.create_role(
                name=role.name, color=role.color, permissions=role.permissions,
                hoist=role.hoist, mentionable=role.mentionable,
                reason="Anti-Nuke recovery: restoring deleted role",
            )
            revert_note = f"Recreated the role **{new_role.name}** (position/members need manual reassignment)."
        except Exception:
            pass
        await self.punish(guild, executor, "role_delete", f"Deleted role {role.name} — {revert_note}")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = after.guild
        active, _ = await self.protection_active(guild.id, "role_update")
        if not active:
            return
        if before.permissions == after.permissions and before.name == after.name and before.color == after.color:
            return
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.role_update, after.id)
        if await self.is_exempt(guild, executor, "role_update"):
            await self._refresh_snapshot(guild)
            return
        if self._debounced(guild.id, f"roleupdate:{after.id}"):
            return
        try:
            await after.edit(
                name=before.name, colour=before.color, permissions=before.permissions,
                hoist=before.hoist, mentionable=before.mentionable,
                reason="Anti-Nuke recovery: reverting role edit",
            )
            revert_note = f"Reverted **{after.name}** back to its previous permissions."
        except Exception:
            revert_note = "Could not revert the role edit."
        await self.punish(guild, executor, "role_update", revert_note)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        active, _ = await self.protection_active(guild.id, "member_update")
        if not active:
            return
        added_roles = [r for r in after.roles if r not in before.roles]
        dangerous = [r for r in added_roles if any(getattr(r.permissions, p) for p in DANGEROUS_PERMS)]
        if not dangerous:
            return
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.member_role_update, after.id)
        if await self.is_exempt(guild, executor, "member_update"):
            return
        if self._debounced(guild.id, f"memberupdate:{after.id}"):
            return
        try:
            await after.remove_roles(*dangerous, reason="Anti-Nuke recovery: stripping unauthorized dangerous role grant")
            revert_note = f"Removed {', '.join(r.name for r in dangerous)} from {after}."
        except Exception:
            revert_note = "Could not remove the granted role(s)."
        await self.punish(guild, executor, "member_update", revert_note)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if not member.bot:
            return
        active, _ = await self.protection_active(guild.id, "bot_add")
        if not active:
            return
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.bot_add, member.id)
        if await self.is_exempt(guild, executor, "bot_add"):
            return
        if self._debounced(guild.id, f"botadd:{member.id}"):
            return
        try:
            await member.kick(reason="Anti-Nuke recovery: removing unauthorized bot")
            revert_note = f"Removed the bot ({member})."
        except Exception:
            revert_note = "Could not remove the bot."
        await self.punish(guild, executor, "bot_add", revert_note)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        active, _ = await self.protection_active(after.id, "guild_update")
        if not active:
            return
        if before.name == after.name and before.vanity_url_code == after.vanity_url_code:
            return
        executor, entry = await self._get_executor(after, discord.AuditLogAction.guild_update)
        if await self.is_exempt(after, executor, "guild_update"):
            return
        if self._debounced(after.id, "guildupdate"):
            return
        try:
            await after.edit(name=before.name, reason="Anti-Nuke recovery: reverting server settings")
            revert_note = f"Reverted server name back to **{before.name}**."
        except Exception:
            revert_note = "Could not revert server settings."
        await self.punish(after, executor, "guild_update", revert_note)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before, after):
        removed = [e for e in before if e not in after]
        added = [e for e in after if e not in before]
        if removed:
            active, _ = await self.protection_active(guild.id, "emoji_delete")
            if active:
                executor, _ = await self._get_executor(guild, discord.AuditLogAction.emoji_delete)
                if not await self.is_exempt(guild, executor, "emoji_delete") and not self._debounced(guild.id, "emojidelete"):
                    await self.punish(guild, executor, "emoji_delete", f"Deleted {len(removed)} emoji(s).")
        if added:
            active, _ = await self.protection_active(guild.id, "emoji_create")
            if active:
                executor, _ = await self._get_executor(guild, discord.AuditLogAction.emoji_create)
                if not await self.is_exempt(guild, executor, "emoji_create") and not self._debounced(guild.id, "emojicreate"):
                    try:
                        for e in added:
                            await e.delete(reason="Anti-Nuke recovery")
                        note = "Deleted the newly added emoji(s)."
                    except Exception:
                        note = "Could not remove the new emoji(s)."
                    await self.punish(guild, executor, "emoji_create", note)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        for key, action in (
            ("webhook_create", discord.AuditLogAction.webhook_create),
            ("webhook_delete", discord.AuditLogAction.webhook_delete),
            ("webhook_update", discord.AuditLogAction.webhook_update),
        ):
            active, _ = await self.protection_active(guild.id, key)
            if not active:
                continue
            executor, entry = await self._get_executor(guild, action)
            if executor is None or await self.is_exempt(guild, executor, key):
                continue
            if self._debounced(guild.id, f"{key}:{channel.id}"):
                continue
            await self.punish(guild, executor, key, f"Webhook change detected in #{channel.name}.")

    @commands.Cog.listener()
    async def on_guild_integrations_update(self, guild: discord.Guild):
        active, _ = await self.protection_active(guild.id, "integration")
        if not active:
            return
        executor, entry = await self._get_executor(guild, discord.AuditLogAction.integration_create)
        if executor is None or await self.is_exempt(guild, executor, "integration"):
            return
        if self._debounced(guild.id, "integration"):
            return
        await self.punish(guild, executor, "integration", "New integration added.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not (message.mention_everyone or message.role_mentions):
            return
        guild = message.guild
        if message.mention_everyone:
            active, _ = await self.protection_active(guild.id, "everyone_ping")
            if active and not await self.is_exempt(guild, message.author, "everyone_ping"):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await self.punish(guild, message.author, "everyone_ping", f"Pinged @everyone/@here in #{message.channel.name}.")
                return
        if message.role_mentions:
            active, _ = await self.protection_active(guild.id, "role_ping")
            if active and not await self.is_exempt(guild, message.author, "role_ping"):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await self.punish(guild, message.author, "role_ping", f"Mass-pinged {len(message.role_mentions)} role(s) in #{message.channel.name}.")

    # ================= COMMANDS =================

    antinuke_group = app_commands.Group(name="antinuke", description="Real-time anti-nuke protection")

    @commands.group(name="antinuke", aliases=["an"], invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antinuke_cmd(self, ctx: commands.Context):
        await ctx.invoke(self.antinuke_status)

    @antinuke_cmd.command(name="status")
    async def antinuke_status(self, ctx: commands.Context):
        settings = await db.get_antinuke(ctx.guild.id)
        active = db.protection_list(settings)
        lines = [f"{'✅' if k in active else '❌'} : {label}" for k, label in PROTECTIONS.items()]
        embed = brand_embed(
            self.bot,
            title=f"🛡️ Anti-Nuke — {'ENABLED' if settings['enabled'] else 'DISABLED'}",
            description="\n".join(lines),
            color=SUCCESS_COLOR if settings["enabled"] else ERROR_COLOR,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @antinuke_cmd.command(name="enable", aliases=["on"])
    @commands.has_permissions(administrator=True)
    async def antinuke_enable(self, ctx: commands.Context):
        if not ctx.guild.me.guild_permissions.administrator:
            embed = brand_embed(
                self.bot,
                description="❌ I need **Administrator** permission for anti-nuke to work reliably "
                            "(otherwise a compromised admin role or a malicious bot could simply out-rank me). "
                            "Grant me Administrator in Server Settings → Roles, then run this again.",
                color=ERROR_COLOR,
            )
            return await ctx.reply(embed=embed, mention_author=False)

        await self._refresh_snapshot(ctx.guild)
        lines_state = ["⬛ " + step for step, _ in ENABLE_STEPS] + ["⬛ Creating Wall role..."]
        embed = brand_embed(self.bot, title="Enabling Security System", description="\n".join(lines_state), color=EMBED_COLOR)
        msg = await ctx.reply(embed=embed, mention_author=False)

        enabled_keys: list[str] = []
        for i, (step_label, keys) in enumerate(ENABLE_STEPS):
            await asyncio.sleep(0.6)
            enabled_keys.extend(keys)
            lines_state[i] = "✅ »  " + step_label
            embed.description = "\n".join(lines_state)
            pct = int(((i + 1) / (len(ENABLE_STEPS) + 1)) * 100)
            embed.set_footer(text=f"{pct}%")
            try:
                await msg.edit(embed=embed)
            except discord.HTTPException:
                pass

        await db.update_antinuke(ctx.guild.id, enabled=1, protections=",".join(ALL_KEYS))

        lines_state[-1] = "🔧 »  Creating Wall role..."
        embed.description = "\n".join(lines_state)
        embed.set_footer(text="100%")
        await msg.edit(embed=embed)

        done_embed = brand_embed(
            self.bot, title="Extra Security Setup",
            description=(
                "**Antinuke has been successfully enabled!**\n\n"
                f"Would you like to set up a **{self.bot.user.name} Wall** role for extra server security?\n\n"
                "*Choosing Yes will create the Wall role, place it as high as possible, and assign it to "
                "all members in the server. For large servers this runs in the background and may take a while.*"
            ),
            color=SUCCESS_COLOR,
        )
        view = WallSetupView(self, ctx.guild.id)
        await ctx.send(embed=done_embed, view=view)

    @antinuke_cmd.command(name="disable", aliases=["off"])
    @commands.has_permissions(administrator=True)
    async def antinuke_disable(self, ctx: commands.Context):
        settings = await db.get_antinuke(ctx.guild.id)
        if not settings["enabled"]:
            return await ctx.reply(embed=brand_embed(self.bot, description="Anti-nuke is already disabled.", color=ERROR_COLOR), mention_author=False)

        steps = [step for step, _ in ENABLE_STEPS]
        lines_state = ["✅ »  " + s.replace("Setting up", "Disabling").replace("Enabling", "Removing").replace("Configuring", "Tearing down").replace("Activating", "Deactivating") for s in steps]
        embed = brand_embed(self.bot, title="⚠️ Shutting Down Security System", description="\n".join(lines_state), color=ERROR_COLOR)
        msg = await ctx.reply(embed=embed, mention_author=False)

        for i in range(len(lines_state)):
            await asyncio.sleep(0.5)
            lines_state[i] = "~~" + lines_state[i][5:] + "~~"
            lines_state[i] = "❌ »  " + lines_state[i]
            embed.description = "\n".join(lines_state)
            pct = int(((i + 1) / len(lines_state)) * 100)
            embed.set_footer(text=f"{pct}%")
            try:
                await msg.edit(embed=embed)
            except discord.HTTPException:
                pass

        await db.update_antinuke(ctx.guild.id, enabled=0)
        embed.set_footer(text="100%")
        await msg.edit(embed=embed)
        await ctx.send(embed=brand_embed(self.bot, description="🔓 Anti-Nuke has been disabled.", color=ERROR_COLOR))

    @antinuke_cmd.command(name="logs")
    @commands.has_permissions(administrator=True)
    async def antinuke_logs(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.update_antinuke(ctx.guild.id, log_channel_id=channel.id)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Anti-nuke logs will be sent to {channel.mention}.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="wall")
    @commands.has_permissions(administrator=True)
    async def wall(self, ctx: commands.Context, action: str = "setup"):
        if action.lower() != "setup":
            return await ctx.reply(embed=brand_embed(self.bot, description="Usage: `wall setup`", color=ERROR_COLOR), mention_author=False)
        await self.build_wall_role(ctx)

    async def build_wall_role(self, ctx_or_interaction):
        guild = ctx_or_interaction.guild
        is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
        send = ctx_or_interaction.followup.send if is_interaction else ctx_or_interaction.send

        try:
            role = await guild.create_role(name=f"{self.bot.user.name} Wall", reason="Anti-Nuke wall setup")
            await role.edit(position=max(guild.me.top_role.position - 1, 1))
        except Exception as e:
            return await send(embed=brand_embed(self.bot, description=f"❌ Couldn't create the Wall role: {e}", color=ERROR_COLOR))

        await db.update_antinuke(guild.id, wall_role_id=role.id)
        status_msg = await send(embed=brand_embed(self.bot, description=f"🧱 Created {role.mention}. Assigning it to all members now (0/{len(guild.members)})...", color=EMBED_COLOR))

        assigned = 0
        for member in guild.members:
            if member.bot:
                continue
            try:
                await member.add_roles(role, reason="Anti-Nuke wall setup")
                assigned += 1
            except discord.HTTPException:
                pass
            if assigned % 25 == 0:
                try:
                    await status_msg.edit(embed=brand_embed(self.bot, description=f"🧱 Assigning {role.mention}... ({assigned}/{len(guild.members)})", color=EMBED_COLOR))
                except discord.HTTPException:
                    pass
            await asyncio.sleep(0.35)  # stay well under Discord's rate limit for large servers

        await status_msg.edit(embed=brand_embed(self.bot, description=f"✅ {role.mention} created and assigned to {assigned} member(s).", color=SUCCESS_COLOR))

    # ---------- whitelist ----------
    @commands.group(name="whitelist", aliases=["wl"], invoke_without_command=True)
    async def whitelist_group(self, ctx: commands.Context):
        await ctx.invoke(self.whitelist_list)

    @whitelist_group.command(name="add")
    @commands.has_permissions(administrator=True)
    async def whitelist_add(self, ctx: commands.Context, user: discord.User):
        await db.add_whitelist(ctx.guild.id, user.id)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ {user.mention} is now whitelisted from all anti-nuke protections.", color=SUCCESS_COLOR), mention_author=False)

    @whitelist_group.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def whitelist_remove(self, ctx: commands.Context, user: discord.User):
        await db.remove_whitelist(ctx.guild.id, user.id)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ {user.mention} removed from the whitelist.", color=SUCCESS_COLOR), mention_author=False)

    @whitelist_group.command(name="list")
    async def whitelist_list(self, ctx: commands.Context):
        rows = await db.list_whitelist(ctx.guild.id)
        if not rows:
            desc = "No one is whitelisted yet.\n\nTo add: `whitelist add @user`\nTo remove: `whitelist remove @user`"
        else:
            lines = []
            for row in rows:
                user = self.bot.get_user(row["user_id"])
                label = user.mention if user else f"`{row['user_id']}`"
                lines.append(f"• {label} — bypasses **{row['bypass']}**")
            desc = "\n".join(lines)
        await ctx.reply(embed=brand_embed(self.bot, title="🔐 Anti-Nuke Whitelist", description=desc, color=EMBED_COLOR), mention_author=False)

    # ---------- slash mirrors (kept alongside mention commands, not replacing them) ----------
    @antinuke_group.command(name="enable", description="Enable anti-nuke protection")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_antinuke_enable(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        await self.antinuke_enable(ctx)

    @antinuke_group.command(name="disable", description="Disable anti-nuke protection")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_antinuke_disable(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        await self.antinuke_disable(ctx)

    @antinuke_group.command(name="status", description="Show anti-nuke protection status")
    async def slash_antinuke_status(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        await self.antinuke_status(ctx)

    @antinuke_group.command(name="whitelist-add", description="Whitelist a user from all anti-nuke protections")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_whitelist_add(self, interaction: discord.Interaction, user: discord.User):
        ctx = await self.bot.get_context(interaction)
        await self.whitelist_add(ctx, user)

    @antinuke_group.command(name="whitelist-remove", description="Remove a user from the anti-nuke whitelist")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_whitelist_remove(self, interaction: discord.Interaction, user: discord.User):
        ctx = await self.bot.get_context(interaction)
        await self.whitelist_remove(ctx, user)


async def setup(bot: commands.Bot):
    await bot.add_cog(Antinuke(bot))
