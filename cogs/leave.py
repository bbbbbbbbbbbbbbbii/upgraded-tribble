import logging
import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import EMBED_COLOR, SUCCESS_COLOR, ERROR_COLOR, DEFAULT_LEAVE_MESSAGE
from utils.placeholders import render
from utils.embeds import brand_embed

log = logging.getLogger("welcomer")


class Leave(commands.Cog):
    """Handles member-leave notifications."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # Wrapped in a broad try/except + logging: previously a bad channel
        # (deleted / missing perms / any other Discord error) would fail
        # silently here with no trace of why the leave message never sent.
        try:
            settings = await db.get_settings(member.guild.id)
            await db.increment(member.guild.id, "total_leaves")

            if not settings["leave_enabled"]:
                return
            if not settings["leave_channel_id"]:
                log.warning("Leave is enabled for guild %s but no leave_channel_id is set.", member.guild.id)
                return

            channel = member.guild.get_channel(settings["leave_channel_id"])
            if channel is None:
                log.warning(
                    "Leave channel %s configured for guild %s no longer exists — "
                    "run /leave channel again to fix this.",
                    settings["leave_channel_id"], member.guild.id,
                )
                return

            text = render(settings["leave_message"], member)
            color = settings["embed_color"] or EMBED_COLOR
            embed = brand_embed(
                self.bot, description=text, color=color,
                footer=f"{member.guild.member_count} members remaining",
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                log.warning(
                    "Missing permission to send the leave message in #%s (guild %s) — "
                    "check I have View Channel + Send Messages + Embed Links there.",
                    channel.name, member.guild.id,
                )
            except discord.HTTPException:
                log.exception("Failed to send leave message in guild %s", member.guild.id)
        except Exception:
            log.exception("Unhandled error in on_member_remove for guild %s", member.guild.id)

    leave_group = app_commands.Group(name="leave", description="Configure leave/goodbye messages")

    @leave_group.command(name="channel", description="Set the channel where leave messages are sent")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update(interaction.guild_id, leave_channel_id=channel.id, leave_enabled=1)
        embed = brand_embed(
            self.bot, description=f"✅ Leave messages will be sent in {channel.mention}.", color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @leave_group.command(name="toggle", description="Enable or disable leave messages")
    @app_commands.choices(state=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggle(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        enabled = 1 if state.value == "on" else 0
        await db.update(interaction.guild_id, leave_enabled=enabled)
        embed = brand_embed(
            self.bot,
            description=f"✅ Leave messages are now **{'enabled' if enabled else 'disabled'}**.",
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @leave_group.command(name="message", description="Set the leave message text (supports placeholders)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_message(self, interaction: discord.Interaction, text: str):
        await db.update(interaction.guild_id, leave_message=text)
        embed = brand_embed(self.bot, title="✅ Leave message updated", description=text, color=SUCCESS_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @leave_group.command(name="reset", description="Reset the leave message back to the default")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset_message(self, interaction: discord.Interaction):
        await db.update(interaction.guild_id, leave_message=DEFAULT_LEAVE_MESSAGE)
        embed = brand_embed(
            self.bot, title="✅ Leave message reset", description=DEFAULT_LEAVE_MESSAGE, color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @leave_group.command(name="test", description="Preview the leave message as if you just left")
    async def test(self, interaction: discord.Interaction):
        settings = await db.get_settings(interaction.guild_id)
        text = render(settings["leave_message"], interaction.user)
        color = settings["embed_color"] or EMBED_COLOR
        embed = brand_embed(self.bot, description=text, color=color, footer="Test preview")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            embed = brand_embed(
                self.bot,
                title="❌ Missing permission",
                description="You need **Manage Server** permission to use this.",
                color=ERROR_COLOR,
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Leave(bot))
