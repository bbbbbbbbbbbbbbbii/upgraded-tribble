"""
VoiceMaster — "Join to Create" temporary voice channels, plus a persistent
button control panel (Lock/Unlock/Hide/Unhide/Rename/Limit+/Limit-/Permit/
Reject/Claim/Disconnect/Mute/Unmute/Transfer/Info) that channel owners use
to manage their own temp channel.

Setup: `/voicemaster setup` (or `@Bot voicemaster setup`) creates the
"Voice Channels" category, the "➕ Join to Create" channel, and posts the
control panel in a "voice-control" text channel — all with the glowing
step-by-step progress embed while it works.
"""
import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import (
    SUCCESS_COLOR,
    ERROR_COLOR,
    EMBED_COLOR,
    VOICEMASTER_CATEGORY_NAME,
    VOICEMASTER_JOIN_CHANNEL_NAME,
    VOICEMASTER_PANEL_CHANNEL_NAME,
    VOICEMASTER_DEFAULT_NAME_TEMPLATE,
)
from utils.embeds import brand_embed, run_step_sequence


def _err(bot, text: str) -> discord.Embed:
    return brand_embed(bot, description=f"❌ {text}", color=ERROR_COLOR)


def _ok(bot, text: str) -> discord.Embed:
    return brand_embed(bot, description=f"✅ {text}", color=SUCCESS_COLOR)


PANEL_EMBED_TITLE = "🔊 Voice Channel Controller"
PANEL_EMBED_DESC = (
    "Use the buttons below to control your temporary voice channel:\n\n"
    "**Access Control**\n"
    "» Lock / Unlock: Restrict / allow access\n"
    "» Hide / Unhide: Toggle channel visibility\n\n"
    "**Configuration**\n"
    "» Rename: Change channel name\n"
    "» Limit +/-: Adjust channel user limit\n\n"
    "**Member Control**\n"
    "» Permit: Allow a user to join\n"
    "» Reject: Block or kick a user\n\n"
    "**Moderation & Ownership**\n"
    "» Mute / Unmute: Change user voice status\n"
    "» Disconnect: Kick user from voice channel\n"
    "» Claim / Transfer: Take or transfer channel ownership"
)


def build_panel_embed(bot: discord.Client) -> discord.Embed:
    embed = brand_embed(bot, title=PANEL_EMBED_TITLE, description=PANEL_EMBED_DESC, color=EMBED_COLOR, timestamp=False)
    embed.set_footer(text="Powered by Welcomer VoiceMaster")
    return embed


class RenameModal(discord.ui.Modal, title="Rename your voice channel"):
    name = discord.ui.TextInput(label="New channel name", max_length=90, min_length=1)

    def __init__(self, channel: discord.VoiceChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.channel.edit(name=str(self.name), reason=f"VoiceMaster rename by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(embed=_err(interaction.client, f"Couldn't rename: {e}"), ephemeral=True)
        await interaction.response.send_message(embed=_ok(interaction.client, f"Renamed to **{self.name}**."), ephemeral=True)


class UserActionSelect(discord.ui.UserSelect):
    """A one-off, ephemeral user picker used for Permit/Reject/Disconnect/Mute/Unmute/Transfer."""

    def __init__(self, action: str, channel: discord.VoiceChannel):
        super().__init__(placeholder=f"Choose a member to {action}...", min_values=1, max_values=1)
        self.action = action
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        target = self.values[0]
        bot = interaction.client
        channel = self.channel
        try:
            if self.action == "permit":
                overwrite = channel.overwrites_for(target)
                overwrite.connect = True
                overwrite.view_channel = True
                await channel.set_permissions(target, overwrite=overwrite, reason=f"VoiceMaster permit by {interaction.user}")
                msg = f"{target.mention} can now join {channel.mention}."
            elif self.action == "reject":
                overwrite = channel.overwrites_for(target)
                overwrite.connect = False
                await channel.set_permissions(target, overwrite=overwrite, reason=f"VoiceMaster reject by {interaction.user}")
                if isinstance(target, discord.Member) and target.voice and target.voice.channel == channel:
                    await target.move_to(None, reason="VoiceMaster reject")
                msg = f"{target.mention} has been blocked from {channel.mention}."
            elif self.action == "disconnect":
                if not (isinstance(target, discord.Member) and target.voice and target.voice.channel == channel):
                    msg = f"{target.mention} isn't in this channel."
                else:
                    await target.move_to(None, reason=f"VoiceMaster disconnect by {interaction.user}")
                    msg = f"{target.mention} was disconnected."
            elif self.action == "mute":
                await target.edit(mute=True, reason=f"VoiceMaster mute by {interaction.user}")
                msg = f"{target.mention} was muted."
            elif self.action == "unmute":
                await target.edit(mute=False, reason=f"VoiceMaster unmute by {interaction.user}")
                msg = f"{target.mention} was unmuted."
            elif self.action == "transfer":
                if not (isinstance(target, discord.Member) and target.voice and target.voice.channel == channel):
                    msg = f"{target.mention} needs to be in the channel to receive ownership."
                else:
                    await db.set_voicemaster_owner(channel.id, target.id)
                    await channel.set_permissions(
                        target, overwrite=discord.PermissionOverwrite(
                            manage_channels=True, move_members=True, mute_members=True, deafen_members=True
                        ),
                        reason="VoiceMaster ownership transfer",
                    )
                    msg = f"Ownership transferred to {target.mention}."
            else:
                msg = "Unknown action."
        except discord.HTTPException as e:
            return await interaction.response.edit_message(embed=_err(bot, f"That didn't work: {e}"), view=None)
        await interaction.response.edit_message(embed=_ok(bot, msg), view=None)


class UserActionView(discord.ui.View):
    def __init__(self, action: str, channel: discord.VoiceChannel):
        super().__init__(timeout=60)
        self.add_item(UserActionSelect(action, channel))


class VoiceMasterPanelView(discord.ui.View):
    """The persistent panel posted once in the control channel (see image 7)."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _resolve(self, interaction: discord.Interaction, *, require_owner: bool = True):
        """Returns (channel, row) if the user is in a valid temp channel (and owns it, if required)."""
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(embed=_err(interaction.client, "Join a voice channel first."), ephemeral=True)
            return None, None
        channel = member.voice.channel
        row = await db.get_voicemaster_channel(channel.id)
        if not row:
            await interaction.response.send_message(embed=_err(interaction.client, "That's not a VoiceMaster temp channel."), ephemeral=True)
            return None, None
        if require_owner and row["owner_id"] != member.id:
            await interaction.response.send_message(embed=_err(interaction.client, "Only the channel owner can do that. Use **Claim** if they've left."), ephemeral=True)
            return None, None
        return channel, row

    async def _prompt_user_select(self, interaction: discord.Interaction, action: str, channel: discord.VoiceChannel):
        await interaction.response.send_message(view=UserActionView(action, channel), ephemeral=True)

    @discord.ui.button(label="Lock", style=discord.ButtonStyle.secondary, custom_id="vm_lock", row=0)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(embed=_ok(interaction.client, "🔒 Channel locked."), ephemeral=True)

    @discord.ui.button(label="Unlock", style=discord.ButtonStyle.secondary, custom_id="vm_unlock", row=0)
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(embed=_ok(interaction.client, "🔓 Channel unlocked."), ephemeral=True)

    @discord.ui.button(label="Hide", style=discord.ButtonStyle.secondary, custom_id="vm_hide", row=0)
    async def hide(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.view_channel = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(embed=_ok(interaction.client, "🙈 Channel hidden."), ephemeral=True)

    @discord.ui.button(label="Unhide", style=discord.ButtonStyle.secondary, custom_id="vm_unhide", row=0)
    async def unhide(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.view_channel = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(embed=_ok(interaction.client, "👁️ Channel visible again."), ephemeral=True)

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, custom_id="vm_rename", row=0)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        await interaction.response.send_modal(RenameModal(channel))

    @discord.ui.button(label="Limit +", style=discord.ButtonStyle.primary, custom_id="vm_limit_up", row=1)
    async def limit_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        new_limit = min(99, (channel.user_limit or 0) + 1)
        await channel.edit(user_limit=new_limit)
        await interaction.response.send_message(embed=_ok(interaction.client, f"👥 User limit set to **{new_limit or 'unlimited'}**."), ephemeral=True)

    @discord.ui.button(label="Limit -", style=discord.ButtonStyle.primary, custom_id="vm_limit_down", row=1)
    async def limit_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        new_limit = max(0, (channel.user_limit or 0) - 1)
        await channel.edit(user_limit=new_limit)
        await interaction.response.send_message(embed=_ok(interaction.client, f"👥 User limit set to **{new_limit or 'unlimited'}**."), ephemeral=True)

    @discord.ui.button(label="Permit", style=discord.ButtonStyle.primary, custom_id="vm_permit", row=1)
    async def permit(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        await self._prompt_user_select(interaction, "permit", channel)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.primary, custom_id="vm_reject", row=1)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        await self._prompt_user_select(interaction, "reject", channel)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="vm_claim", row=1)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, row = await self._resolve(interaction, require_owner=False)
        if not channel:
            return
        current_owner = interaction.guild.get_member(row["owner_id"])
        if current_owner and current_owner.voice and current_owner.voice.channel == channel:
            return await interaction.response.send_message(embed=_err(interaction.client, "The current owner is still in the channel."), ephemeral=True)
        await db.set_voicemaster_owner(channel.id, interaction.user.id)
        await channel.set_permissions(
            interaction.user, overwrite=discord.PermissionOverwrite(
                manage_channels=True, move_members=True, mute_members=True, deafen_members=True
            ),
        )
        await interaction.response.send_message(embed=_ok(interaction.client, "👑 You're now the owner of this channel."), ephemeral=True)

    @discord.ui.button(label="Disconnect", style=discord.ButtonStyle.danger, custom_id="vm_disconnect", row=2)
    async def disconnect(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        await self._prompt_user_select(interaction, "disconnect", channel)

    @discord.ui.button(label="Mute", style=discord.ButtonStyle.danger, custom_id="vm_mute", row=2)
    async def mute(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        await self._prompt_user_select(interaction, "mute", channel)

    @discord.ui.button(label="Unmute", style=discord.ButtonStyle.danger, custom_id="vm_unmute", row=2)
    async def unmute(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        await self._prompt_user_select(interaction, "unmute", channel)

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.success, custom_id="vm_transfer", row=2)
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel, _ = await self._resolve(interaction)
        if not channel:
            return
        await self._prompt_user_select(interaction, "transfer", channel)

    @discord.ui.button(label="Info", style=discord.ButtonStyle.secondary, custom_id="vm_info", row=2)
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not member.voice or not member.voice.channel:
            return await interaction.response.send_message(embed=_err(interaction.client, "Join a voice channel first."), ephemeral=True)
        channel = member.voice.channel
        row = await db.get_voicemaster_channel(channel.id)
        if not row:
            return await interaction.response.send_message(embed=_err(interaction.client, "That's not a VoiceMaster temp channel."), ephemeral=True)
        owner = interaction.guild.get_member(row["owner_id"])
        locked = channel.overwrites_for(interaction.guild.default_role).connect is False
        hidden = channel.overwrites_for(interaction.guild.default_role).view_channel is False
        embed = brand_embed(
            interaction.client, title=f"ℹ️ {channel.name}",
            description=(
                f"**Owner:** {owner.mention if owner else 'Unknown'}\n"
                f"**Members:** {len(channel.members)} / {channel.user_limit or '∞'}\n"
                f"**Locked:** {'Yes' if locked else 'No'}\n"
                f"**Hidden:** {'Yes' if hidden else 'No'}"
            ),
            color=EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def perform_voicemaster_setup(bot: commands.Bot, guild: discord.Guild) -> tuple[discord.VoiceChannel, discord.TextChannel]:
    """Creates (or reuses) the category/join-channel/panel-channel and posts
    the control panel. Shared by both the /voicemaster setup slash command
    and the '@Bot voicemaster setup' mention command."""
    category = discord.utils.get(guild.categories, name=VOICEMASTER_CATEGORY_NAME)
    if category is None:
        category = await guild.create_category(VOICEMASTER_CATEGORY_NAME, reason="VoiceMaster setup")

    join_channel = discord.utils.get(category.voice_channels, name=VOICEMASTER_JOIN_CHANNEL_NAME)
    if join_channel is None:
        join_channel = await guild.create_voice_channel(
            VOICEMASTER_JOIN_CHANNEL_NAME, category=category, reason="VoiceMaster setup"
        )

    panel_channel = discord.utils.get(guild.text_channels, name=VOICEMASTER_PANEL_CHANNEL_NAME)
    if panel_channel is None:
        panel_channel = await guild.create_text_channel(
            VOICEMASTER_PANEL_CHANNEL_NAME, category=category, reason="VoiceMaster setup",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(send_messages=False, view_channel=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(send_messages=True, view_channel=True, embed_links=True),
            },
        )

    panel_message = await panel_channel.send(embed=build_panel_embed(bot), view=VoiceMasterPanelView())

    await db.update_voicemaster(
        guild.id,
        join_channel_id=join_channel.id,
        category_id=category.id,
        panel_channel_id=panel_channel.id,
        panel_message_id=panel_message.id,
    )
    return join_channel, panel_channel


class VoiceMaster(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Re-register the persistent view so buttons on old panel messages
        # keep working after a restart.
        self.bot.add_view(VoiceMasterPanelView())

    # ---------- Join to Create enforcement ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        settings = await db.get_voicemaster(member.guild.id)
        join_channel_id = settings.get("join_channel_id")

        # created a temp channel by joining the "Join to Create" hub
        if join_channel_id and after.channel and after.channel.id == join_channel_id:
            category = member.guild.get_channel(settings["category_id"]) if settings["category_id"] else after.channel.category
            template = settings.get("name_template") or VOICEMASTER_DEFAULT_NAME_TEMPLATE
            name = template.format(user=member.display_name)[:90]
            try:
                new_channel = await member.guild.create_voice_channel(
                    name=name,
                    category=category,
                    reason=f"VoiceMaster: temp channel for {member}",
                    overwrites={
                        member: discord.PermissionOverwrite(
                            manage_channels=True, move_members=True, mute_members=True, deafen_members=True
                        ),
                    },
                )
                await db.add_voicemaster_channel(new_channel.id, member.guild.id, member.id)
                await member.move_to(new_channel, reason="VoiceMaster: moved into new temp channel")
            except discord.HTTPException:
                pass

        # clean up an empty temp channel
        if before.channel is not None and before.channel != after.channel:
            row = await db.get_voicemaster_channel(before.channel.id)
            if row and len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="VoiceMaster: temp channel empty")
                except discord.HTTPException:
                    pass
                await db.remove_voicemaster_channel(before.channel.id)

    # ---------- setup ----------
    voicemaster_group = app_commands.Group(
        name="voicemaster", description="Join-to-create temporary voice channels",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @voicemaster_group.command(name="setup", description="Set up Join-to-Create temp voice channels + control panel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_cmd(self, interaction: discord.Interaction):
        if not interaction.guild.me.guild_permissions.manage_channels:
            return await interaction.response.send_message(embed=_err(self.bot, "I need **Manage Channels** permission for this."), ephemeral=True)

        await interaction.response.send_message(embed=brand_embed(self.bot, description="⚡ Booting up...", color=EMBED_COLOR))
        msg = await interaction.original_response()
        steps = [
            "Initializing VoiceMaster Core...",
            "Creating Voice Channels category...",
            "Creating Join-to-Create channel...",
            "Deploying the control panel...",
            "Finalizing configuration...",
        ]
        await run_step_sequence(msg, self.bot, "Setting Up VoiceMaster", steps, emoji="🔊")

        join_channel, panel_channel = await perform_voicemaster_setup(self.bot, interaction.guild)

        done = brand_embed(
            self.bot,
            title="✅ VoiceMaster is set up!",
            description=(
                f"**Join to create:** {join_channel.mention} — join it to get your own temp voice channel\n"
                f"**Control panel:** {panel_channel.mention}\n\n"
                "Change the naming template with `/voicemaster nametemplate`."
            ),
            color=SUCCESS_COLOR,
        )
        await msg.edit(embed=done)

    @voicemaster_group.command(name="nametemplate", description="Set the name given to new temp channels (use {user})")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def nametemplate(self, interaction: discord.Interaction, template: str):
        if "{user}" not in template:
            template += " — {user}"
        await db.update_voicemaster(interaction.guild_id, name_template=template)
        await interaction.response.send_message(embed=_ok(self.bot, f"New temp channels will be named like: `{template.format(user=interaction.user.display_name)}`"), ephemeral=True)

    @voicemaster_group.command(name="panel", description="Re-post the VoiceMaster control panel in this channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction):
        message = await interaction.channel.send(embed=build_panel_embed(self.bot), view=VoiceMasterPanelView())
        await db.update_voicemaster(interaction.guild_id, panel_channel_id=interaction.channel.id, panel_message_id=message.id)
        await interaction.response.send_message(embed=_ok(self.bot, "Panel posted."), ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            embed = _err(self.bot, "You need **Manage Server** permission to use this.")
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceMaster(bot))
