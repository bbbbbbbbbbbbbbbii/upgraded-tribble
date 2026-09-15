import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import (
    EMBED_COLOR,
    SUCCESS_COLOR,
    ERROR_COLOR,
    PLACEHOLDERS_HELP,
    DEFAULT_WELCOME_MESSAGE,
    DEFAULT_DM_MESSAGE,
)
from utils.placeholders import render
from utils.image_gen import generate_card
from utils.embeds import brand_embed


class Welcome(commands.Cog):
    """Handles new-member welcomes: channel message, image card, DM, and autorole."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- event ----------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        settings = await db.get_settings(member.guild.id)
        await db.increment(member.guild.id, "total_joins")

        # autorole
        role_ids = db.role_ids(settings)
        if role_ids:
            roles = [member.guild.get_role(rid) for rid in role_ids]
            roles = [r for r in roles if r is not None]
            if roles:
                try:
                    await member.add_roles(*roles, reason="Autorole on join")
                except discord.Forbidden:
                    pass

        # channel welcome
        if settings["welcome_enabled"] and settings["welcome_channel_id"]:
            channel = member.guild.get_channel(settings["welcome_channel_id"])
            if channel:
                text = render(settings["welcome_message"], member)
                color = settings["embed_color"] or EMBED_COLOR
                file = None
                embed = brand_embed(
                    self.bot, description=text, color=color,
                    footer=f"Member #{member.guild.member_count}",
                )

                if settings["welcome_card"]:
                    try:
                        buf = await generate_card(
                            username=member.display_name,
                            subtitle=f"Member #{member.guild.member_count} of {member.guild.name}",
                            avatar_url=member.display_avatar.replace(size=256).url,
                            background_url=settings["welcome_bg_url"],
                            accent_rgb=_hex_to_rgb(color),
                        )
                        file = discord.File(buf, filename="welcome.png")
                        embed.set_image(url="attachment://welcome.png")
                    except Exception:
                        file = None

                try:
                    if file:
                        await channel.send(content=member.mention, embed=embed, file=file)
                    else:
                        await channel.send(content=member.mention, embed=embed)
                except discord.Forbidden:
                    pass

        # DM welcome
        if settings["dm_enabled"]:
            try:
                dm_text = render(settings["dm_message"], member)
                await member.send(dm_text)
            except (discord.Forbidden, discord.HTTPException):
                pass

    # ---------- command group ----------
    welcome_group = app_commands.Group(name="welcome", description="Configure welcome messages")

    @welcome_group.command(name="channel", description="Set the channel where welcome messages are sent")
    @app_commands.describe(channel="Channel to send welcome messages in")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update(interaction.guild_id, welcome_channel_id=channel.id, welcome_enabled=1)
        embed = brand_embed(
            self.bot,
            description=f"✅ Welcome messages will be sent in {channel.mention}.",
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(name="toggle", description="Enable or disable welcome messages")
    @app_commands.describe(state="on to enable, off to disable")
    @app_commands.choices(state=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggle(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        enabled = 1 if state.value == "on" else 0
        await db.update(interaction.guild_id, welcome_enabled=enabled)
        embed = brand_embed(
            self.bot,
            description=f"✅ Welcome messages are now **{'enabled' if enabled else 'disabled'}**.",
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(name="message", description="Set the welcome message text (supports placeholders)")
    @app_commands.describe(text="The message to send. Use /welcome placeholders to see available variables")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_message(self, interaction: discord.Interaction, text: str):
        await db.update(interaction.guild_id, welcome_message=text)
        embed = brand_embed(
            self.bot, title="✅ Welcome message updated", description=text, color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(name="reset", description="Reset the welcome message back to the default")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset_message(self, interaction: discord.Interaction):
        await db.update(interaction.guild_id, welcome_message=DEFAULT_WELCOME_MESSAGE)
        embed = brand_embed(
            self.bot,
            title="✅ Welcome message reset",
            description=DEFAULT_WELCOME_MESSAGE,
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(name="card", description="Enable or disable the generated welcome image card")
    @app_commands.choices(state=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggle_card(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        enabled = 1 if state.value == "on" else 0
        await db.update(interaction.guild_id, welcome_card=enabled)
        embed = brand_embed(
            self.bot,
            description=f"✅ Welcome image card is now **{'enabled' if enabled else 'disabled'}**.",
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(name="background", description="Set a custom background image URL for the welcome card")
    @app_commands.describe(url="Direct image URL, or 'reset' to clear it")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_background(self, interaction: discord.Interaction, url: str):
        value = None if url.lower() == "reset" else url
        await db.update(interaction.guild_id, welcome_bg_url=value)
        embed = brand_embed(
            self.bot,
            description="✅ Background updated." if value else "✅ Background reset to default gradient.",
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(name="dm", description="Enable/disable and configure a DM sent to new members")
    @app_commands.describe(state="on/off", text="Optional new DM message text")
    @app_commands.choices(state=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dm_config(self, interaction: discord.Interaction, state: app_commands.Choice[str], text: str = None):
        fields = {"dm_enabled": 1 if state.value == "on" else 0}
        if text:
            fields["dm_message"] = text
        await db.update(interaction.guild_id, **fields)
        embed = brand_embed(
            self.bot,
            description=f"✅ Welcome DM is now **{'enabled' if fields['dm_enabled'] else 'disabled'}**.",
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(name="dm-reset", description="Reset the welcome DM message back to the default")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dm_reset(self, interaction: discord.Interaction):
        await db.update(interaction.guild_id, dm_message=DEFAULT_DM_MESSAGE)
        embed = brand_embed(
            self.bot, title="✅ Welcome DM reset", description=DEFAULT_DM_MESSAGE, color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(name="test", description="Preview the welcome message/card as if you just joined")
    async def test(self, interaction: discord.Interaction):
        settings = await db.get_settings(interaction.guild_id)
        member = interaction.user
        text = render(settings["welcome_message"], member)
        color = settings["embed_color"] or EMBED_COLOR
        embed = brand_embed(
            self.bot, description=text, color=color,
            footer=f"Member #{interaction.guild.member_count} • Test preview",
        )

        await interaction.response.defer(ephemeral=True)
        file = None
        if settings["welcome_card"]:
            try:
                buf = await generate_card(
                    username=member.display_name,
                    subtitle=f"Member #{interaction.guild.member_count} of {interaction.guild.name}",
                    avatar_url=member.display_avatar.replace(size=256).url,
                    background_url=settings["welcome_bg_url"],
                    accent_rgb=_hex_to_rgb(color),
                )
                file = discord.File(buf, filename="welcome.png")
                embed.set_image(url="attachment://welcome.png")
            except Exception:
                file = None

        if file:
            await interaction.followup.send(embed=embed, file=file, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    @welcome_group.command(name="placeholders", description="List available placeholders for welcome/leave messages")
    async def placeholders(self, interaction: discord.Interaction):
        embed = brand_embed(
            self.bot, title="📋 Available Placeholders", description=PLACEHOLDERS_HELP, color=EMBED_COLOR,
        )
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


def _hex_to_rgb(color) -> tuple[int, int, int]:
    if isinstance(color, discord.Colour):
        value = color.value
    else:
        value = int(color)
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
