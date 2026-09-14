"""
Voice logging: announces when a member joins, leaves, or switches voice channels.
"""
import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import SUCCESS_COLOR, ERROR_COLOR, EMBED_COLOR
from utils.embeds import brand_embed


class VoiceLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or before.channel == after.channel:
            return
        settings = await db.get_voicelog(member.guild.id)
        if not settings["enabled"] or not settings["channel_id"]:
            return
        channel = member.guild.get_channel(settings["channel_id"])
        if not channel:
            return

        if before.channel is None and after.channel is not None:
            desc = f"🔊 {member.mention} joined **{after.channel.name}**."
        elif before.channel is not None and after.channel is None:
            desc = f"🔇 {member.mention} left **{before.channel.name}**."
        else:
            desc = f"🔀 {member.mention} moved from **{before.channel.name}** to **{after.channel.name}**."

        embed = brand_embed(self.bot, description=desc, color=EMBED_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    voicelog_group = app_commands.Group(
        name="voicelog", description="Log members joining/leaving voice channels",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @voicelog_group.command(name="channel", description="Set the channel voice join/leave logs are sent to")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update_voicelog(interaction.guild_id, channel_id=channel.id, enabled=1)
        embed = brand_embed(self.bot, description=f"✅ Voice logs will be sent in {channel.mention}.", color=SUCCESS_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @voicelog_group.command(name="toggle", description="Enable or disable voice logging")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggle(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        await db.update_voicelog(interaction.guild_id, enabled=1 if state.value == "on" else 0)
        embed = brand_embed(self.bot, description=f"✅ Voice logging is now **{state.value}**.", color=SUCCESS_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            embed = brand_embed(self.bot, title="❌ Missing permission", description="You need **Manage Server** permission to use this.", color=ERROR_COLOR)
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceLog(bot))
