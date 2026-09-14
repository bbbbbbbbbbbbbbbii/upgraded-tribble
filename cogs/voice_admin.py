"""
Voice moderation toolkit — the `/voice` command group plus `/vcban`,
`/vcmod`, and `/vcrole`.

Anyone with a relevant Discord voice permission (Move/Mute/Deafen Members,
Moderate Members, or Manage Channels) can use these, and so can anyone
added via `/vcmod add` — a per-server allowlist for people you trust with
voice moderation without handing them full server permissions.
"""
import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import SUCCESS_COLOR, ERROR_COLOR, EMBED_COLOR
from utils.embeds import brand_embed


def _err(bot, text: str) -> discord.Embed:
    return brand_embed(bot, description=f"❌ {text}", color=ERROR_COLOR)


def _ok(bot, text: str) -> discord.Embed:
    return brand_embed(bot, description=f"✅ {text}", color=SUCCESS_COLOR)


async def _has_voice_perms(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    perms = member.guild_permissions
    if perms.administrator or perms.manage_guild or perms.moderate_members or perms.manage_channels:
        return True
    if perms.mute_members or perms.deafen_members or perms.move_members:
        return True
    return await db.is_vcmod(interaction.guild_id, member.id)


def _voice_channel_of(member: discord.Member) -> discord.VoiceChannel | None:
    return member.voice.channel if member.voice else None


class VoicePermCheck(app_commands.CheckFailure):
    pass


def voice_perm_required():
    async def predicate(interaction: discord.Interaction) -> bool:
        if await _has_voice_perms(interaction):
            return True
        raise VoicePermCheck("missing voice mod permission")
    return app_commands.check(predicate)


class VoiceAdmin(commands.Cog):
    """/voice, /vcban, /vcmod, /vcrole"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- enforcement: vcban + vcrole ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # vcban: kick a banned user straight back out if they try to join any VC
        if after.channel is not None and before.channel != after.channel:
            if await db.is_vcbanned(member.guild.id, member.id):
                try:
                    await member.move_to(None, reason="Voice-banned")
                except discord.HTTPException:
                    pass
                return

        # vcrole: grant/remove a role tied to a specific voice channel
        if before.channel != after.channel:
            if before.channel is not None:
                role_id = await db.get_vcrole(member.guild.id, before.channel.id)
                if role_id:
                    role = member.guild.get_role(role_id)
                    if role and role in member.roles:
                        try:
                            await member.remove_roles(role, reason="Left VC tied to vcrole")
                        except discord.HTTPException:
                            pass
            if after.channel is not None:
                role_id = await db.get_vcrole(member.guild.id, after.channel.id)
                if role_id:
                    role = member.guild.get_role(role_id)
                    if role and role not in member.roles:
                        try:
                            await member.add_roles(role, reason="Joined VC tied to vcrole")
                        except discord.HTTPException:
                            pass

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, VoicePermCheck):
            embed = _err(self.bot, "You need a voice-moderation permission (Move/Mute/Deafen Members, Manage Channels) or to be added with `/vcmod add`.")
        elif isinstance(error, app_commands.MissingPermissions):
            embed = _err(self.bot, "You need **Manage Server** permission to use this.")
        else:
            raise error
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    # ================= /voice =================
    voice_group = app_commands.Group(name="voice", description="Voice channel moderation")

    @voice_group.command(name="kick", description="Disconnect a member from voice")
    @voice_perm_required()
    async def kick(self, interaction: discord.Interaction, member: discord.Member):
        if not member.voice:
            return await interaction.response.send_message(embed=_err(self.bot, f"{member.mention} isn't in a voice channel."), ephemeral=True)
        await member.move_to(None, reason=f"Voice kick by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"{member.mention} was disconnected from voice."))

    @voice_group.command(name="kickall", description="Disconnect everyone from a voice channel")
    @app_commands.describe(channel="Defaults to your current voice channel")
    @voice_perm_required()
    async def kickall(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        channel = channel or _voice_channel_of(interaction.user)
        if not channel:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel or specify one."), ephemeral=True)
        members = list(channel.members)
        for m in members:
            try:
                await m.move_to(None, reason=f"Voice kickall by {interaction.user}")
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=_ok(self.bot, f"Disconnected **{len(members)}** member(s) from {channel.mention}."))

    @voice_group.command(name="mute", description="Server-mute a member in voice")
    @voice_perm_required()
    async def mute(self, interaction: discord.Interaction, member: discord.Member):
        if not member.voice:
            return await interaction.response.send_message(embed=_err(self.bot, f"{member.mention} isn't in a voice channel."), ephemeral=True)
        await member.edit(mute=True, reason=f"Voice mute by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"{member.mention} has been voice-muted."))

    @voice_group.command(name="muteall", description="Server-mute everyone in a voice channel")
    @app_commands.describe(channel="Defaults to your current voice channel")
    @voice_perm_required()
    async def muteall(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        channel = channel or _voice_channel_of(interaction.user)
        if not channel:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel or specify one."), ephemeral=True)
        for m in channel.members:
            try:
                await m.edit(mute=True, reason=f"Voice muteall by {interaction.user}")
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=_ok(self.bot, f"Muted everyone in {channel.mention}."))

    @voice_group.command(name="unmute", description="Remove a member's voice mute")
    @voice_perm_required()
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        await member.edit(mute=False, reason=f"Voice unmute by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"{member.mention} has been unmuted."))

    @voice_group.command(name="unmuteall", description="Remove the voice mute from everyone in a channel")
    @app_commands.describe(channel="Defaults to your current voice channel")
    @voice_perm_required()
    async def unmuteall(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        channel = channel or _voice_channel_of(interaction.user)
        if not channel:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel or specify one."), ephemeral=True)
        for m in channel.members:
            try:
                await m.edit(mute=False, reason=f"Voice unmuteall by {interaction.user}")
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=_ok(self.bot, f"Unmuted everyone in {channel.mention}."))

    @voice_group.command(name="deafen", description="Server-deafen a member in voice")
    @voice_perm_required()
    async def deafen(self, interaction: discord.Interaction, member: discord.Member):
        if not member.voice:
            return await interaction.response.send_message(embed=_err(self.bot, f"{member.mention} isn't in a voice channel."), ephemeral=True)
        await member.edit(deafen=True, reason=f"Voice deafen by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"{member.mention} has been voice-deafened."))

    @voice_group.command(name="deafenall", description="Server-deafen everyone in a voice channel")
    @app_commands.describe(channel="Defaults to your current voice channel")
    @voice_perm_required()
    async def deafenall(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        channel = channel or _voice_channel_of(interaction.user)
        if not channel:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel or specify one."), ephemeral=True)
        for m in channel.members:
            try:
                await m.edit(deafen=True, reason=f"Voice deafenall by {interaction.user}")
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=_ok(self.bot, f"Deafened everyone in {channel.mention}."))

    @voice_group.command(name="undeafen", description="Remove a member's voice deafen")
    @voice_perm_required()
    async def undeafen(self, interaction: discord.Interaction, member: discord.Member):
        await member.edit(deafen=False, reason=f"Voice undeafen by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"{member.mention} has been undeafened."))

    @voice_group.command(name="undeafenall", description="Remove the voice deafen from everyone in a channel")
    @app_commands.describe(channel="Defaults to your current voice channel")
    @voice_perm_required()
    async def undeafenall(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        channel = channel or _voice_channel_of(interaction.user)
        if not channel:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel or specify one."), ephemeral=True)
        for m in channel.members:
            try:
                await m.edit(deafen=False, reason=f"Voice undeafenall by {interaction.user}")
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=_ok(self.bot, f"Undeafened everyone in {channel.mention}."))

    @voice_group.command(name="move", description="Move a member to another voice channel")
    @voice_perm_required()
    async def move(self, interaction: discord.Interaction, member: discord.Member, channel: discord.VoiceChannel):
        if not member.voice:
            return await interaction.response.send_message(embed=_err(self.bot, f"{member.mention} isn't in a voice channel."), ephemeral=True)
        await member.move_to(channel, reason=f"Voice move by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"Moved {member.mention} to {channel.mention}."))

    @voice_group.command(name="moveall", description="Move everyone from your current voice channel to another")
    @voice_perm_required()
    async def moveall(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        source = _voice_channel_of(interaction.user)
        if not source:
            return await interaction.response.send_message(embed=_err(self.bot, "Join the voice channel you want to move everyone from."), ephemeral=True)
        members = list(source.members)
        for m in members:
            try:
                await m.move_to(channel, reason=f"Voice moveall by {interaction.user}")
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=_ok(self.bot, f"Moved **{len(members)}** member(s) to {channel.mention}."))

    @voice_group.command(name="pull", description="Pull a member into your current voice channel")
    @voice_perm_required()
    async def pull(self, interaction: discord.Interaction, member: discord.Member):
        dest = _voice_channel_of(interaction.user)
        if not dest:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel first."), ephemeral=True)
        if not member.voice:
            return await interaction.response.send_message(embed=_err(self.bot, f"{member.mention} isn't in a voice channel."), ephemeral=True)
        await member.move_to(dest, reason=f"Voice pull by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"Pulled {member.mention} into {dest.mention}."))

    @voice_group.command(name="pullall", description="Pull everyone from another voice channel into yours")
    @voice_perm_required()
    async def pullall(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        dest = _voice_channel_of(interaction.user)
        if not dest:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel first."), ephemeral=True)
        members = list(channel.members)
        for m in members:
            try:
                await m.move_to(dest, reason=f"Voice pullall by {interaction.user}")
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=_ok(self.bot, f"Pulled **{len(members)}** member(s) from {channel.mention} into {dest.mention}."))

    @voice_group.command(name="lock", description="Lock your current voice channel (no one new can join)")
    @voice_perm_required()
    async def lock(self, interaction: discord.Interaction):
        channel = _voice_channel_of(interaction.user)
        if not channel:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel first."), ephemeral=True)
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Voice lock by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"🔒 {channel.mention} is now locked."))

    @voice_group.command(name="unlock", description="Unlock your current voice channel")
    @voice_perm_required()
    async def unlock(self, interaction: discord.Interaction):
        channel = _voice_channel_of(interaction.user)
        if not channel:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel first."), ephemeral=True)
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Voice unlock by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"🔓 {channel.mention} is now unlocked."))

    @voice_group.command(name="private", description="Lock AND hide your current voice channel")
    @voice_perm_required()
    async def private(self, interaction: discord.Interaction):
        channel = _voice_channel_of(interaction.user)
        if not channel:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel first."), ephemeral=True)
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = False
        overwrite.view_channel = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Voice private by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"🙈 {channel.mention} is now private (locked + hidden)."))

    @voice_group.command(name="unprivate", description="Undo /voice private")
    @voice_perm_required()
    async def unprivate(self, interaction: discord.Interaction):
        channel = _voice_channel_of(interaction.user)
        if not channel:
            return await interaction.response.send_message(embed=_err(self.bot, "Join a voice channel first."), ephemeral=True)
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = None
        overwrite.view_channel = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Voice unprivate by {interaction.user}")
        await interaction.response.send_message(embed=_ok(self.bot, f"👁️ {channel.mention} is visible and joinable again."))

    # ================= /vcban =================
    vcban_group = app_commands.Group(name="vcban", description="Ban a member from joining any voice channel")

    @vcban_group.command(name="add", description="Voice-ban a member")
    @voice_perm_required()
    async def vcban_add(self, interaction: discord.Interaction, member: discord.Member):
        await db.add_vcban(interaction.guild_id, member.id)
        if member.voice:
            try:
                await member.move_to(None, reason=f"Voice-banned by {interaction.user}")
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=_ok(self.bot, f"🚫 {member.mention} is now voice-banned."))

    @vcban_group.command(name="remove", description="Remove a member's voice ban")
    @voice_perm_required()
    async def vcban_remove(self, interaction: discord.Interaction, member: discord.Member):
        await db.remove_vcban(interaction.guild_id, member.id)
        await interaction.response.send_message(embed=_ok(self.bot, f"{member.mention} is no longer voice-banned."))

    @vcban_group.command(name="list", description="List everyone who is voice-banned")
    async def vcban_list(self, interaction: discord.Interaction):
        ids = await db.list_vcbans(interaction.guild_id)
        desc = "\n".join(f"• <@{i}>" for i in ids) if ids else "No one is voice-banned."
        await interaction.response.send_message(embed=brand_embed(self.bot, title="🚫 Voice bans", description=desc, color=EMBED_COLOR))

    # ================= /vcmod =================
    vcmod_group = app_commands.Group(
        name="vcmod", description="Manage who can use /voice commands",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @vcmod_group.command(name="add", description="Let a member use /voice commands")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def vcmod_add(self, interaction: discord.Interaction, member: discord.Member):
        await db.add_vcmod(interaction.guild_id, member.id)
        await interaction.response.send_message(embed=_ok(self.bot, f"{member.mention} can now use `/voice` commands."))

    @vcmod_group.command(name="remove", description="Revoke a member's /voice access")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def vcmod_remove(self, interaction: discord.Interaction, member: discord.Member):
        await db.remove_vcmod(interaction.guild_id, member.id)
        await interaction.response.send_message(embed=_ok(self.bot, f"{member.mention}'s `/voice` access was revoked."))

    @vcmod_group.command(name="list", description="List everyone with /voice access via vcmod")
    async def vcmod_list(self, interaction: discord.Interaction):
        ids = await db.list_vcmods(interaction.guild_id)
        desc = "\n".join(f"• <@{i}>" for i in ids) if ids else "No extra voice mods configured."
        await interaction.response.send_message(embed=brand_embed(self.bot, title="🎚️ Voice mods", description=desc, color=EMBED_COLOR))

    # ================= /vcrole =================
    vcrole_group = app_commands.Group(
        name="vcrole", description="Auto-grant a role while connected to a specific voice channel",
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @vcrole_group.command(name="add", description="Tie a role to a voice channel")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def vcrole_add(self, interaction: discord.Interaction, channel: discord.VoiceChannel, role: discord.Role):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(embed=_err(self.bot, "That role is higher than or equal to my top role."), ephemeral=True)
        await db.set_vcrole(interaction.guild_id, channel.id, role.id)
        await interaction.response.send_message(embed=_ok(self.bot, f"{role.mention} will now be given to anyone in {channel.mention}."))

    @vcrole_group.command(name="remove", description="Remove a channel's vcrole mapping")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def vcrole_remove(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        await db.remove_vcrole(interaction.guild_id, channel.id)
        await interaction.response.send_message(embed=_ok(self.bot, f"Removed the vcrole mapping for {channel.mention}."))

    @vcrole_group.command(name="config", description="List all vcrole mappings")
    async def vcrole_config(self, interaction: discord.Interaction):
        rows = await db.list_vcroles(interaction.guild_id)
        if not rows:
            desc = "No vcrole mappings configured. Use `/vcrole add` to create one."
        else:
            lines = []
            for r in rows:
                ch = interaction.guild.get_channel(r["channel_id"])
                role = interaction.guild.get_role(r["role_id"])
                lines.append(f"• {ch.mention if ch else '#deleted-channel'} → {role.mention if role else '@deleted-role'}")
            desc = "\n".join(lines)
        await interaction.response.send_message(embed=brand_embed(self.bot, title="🎭 VC roles", description=desc, color=EMBED_COLOR))


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceAdmin(bot))
