import time
import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import EMBED_COLOR, SUCCESS_COLOR, ERROR_COLOR
from utils.embeds import brand_embed, loading_embed


class General(commands.Cog):
    """Core utility commands: config overview, embed color, stats, help, ping."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- /config ----------
    @app_commands.command(name="config", description="View the current welcomer configuration for this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_view(self, interaction: discord.Interaction):
        s = await db.get_settings(interaction.guild_id)
        guild = interaction.guild

        w_channel = guild.get_channel(s["welcome_channel_id"]) if s["welcome_channel_id"] else None
        l_channel = guild.get_channel(s["leave_channel_id"]) if s["leave_channel_id"] else None
        roles = [guild.get_role(r) for r in db.role_ids(s)]
        roles = [r.mention for r in roles if r]

        embed = brand_embed(self.bot, title="⚙️ Welcomer Configuration", color=EMBED_COLOR)
        embed.add_field(
            name="👋 Welcome",
            value=(
                f"Status: **{'✅ On' if s['welcome_enabled'] else '❌ Off'}**\n"
                f"Channel: {w_channel.mention if w_channel else '*not set*'}\n"
                f"Image card: **{'On' if s['welcome_card'] else 'Off'}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🚪 Leave",
            value=(
                f"Status: **{'✅ On' if s['leave_enabled'] else '❌ Off'}**\n"
                f"Channel: {l_channel.mention if l_channel else '*not set*'}"
            ),
            inline=True,
        )
        embed.add_field(
            name="📩 DM Welcome",
            value=f"Status: **{'✅ On' if s['dm_enabled'] else '❌ Off'}**",
            inline=True,
        )
        embed.add_field(
            name="🎭 Autoroles",
            value=", ".join(roles) if roles else "*none set*",
            inline=False,
        )
        embed.add_field(name="Total Joins Tracked", value=str(s["total_joins"]), inline=True)
        embed.add_field(name="Total Leaves Tracked", value=str(s["total_leaves"]), inline=True)
        embed.set_footer(
            text="Use /welcome, /leave, /autorole, or /autosetup to change these settings",
            icon_url=self.bot.user.display_avatar.url if self.bot.user else None,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- /embed-color ----------
    @app_commands.command(name="embed-color", description="Set the embed color used for welcome/leave messages")
    @app_commands.describe(hex_color="Hex color like #5865F2, or 'reset' for default")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def embed_color(self, interaction: discord.Interaction, hex_color: str):
        if hex_color.lower() == "reset":
            await db.update(interaction.guild_id, embed_color=None)
            embed = brand_embed(self.bot, description="✅ Embed color reset to default.", color=SUCCESS_COLOR)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        cleaned = hex_color.strip().lstrip("#")
        try:
            value = int(cleaned, 16)
            if not (0 <= value <= 0xFFFFFF):
                raise ValueError
        except ValueError:
            embed = brand_embed(self.bot, description="❌ Invalid hex color. Example: `#5865F2`", color=ERROR_COLOR)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await db.update(interaction.guild_id, embed_color=value)
        embed = brand_embed(self.bot, description="✅ Embed color updated.", color=value)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- /stats ----------
    @app_commands.command(name="stats", description="Show join/leave stats tracked by the bot for this server")
    async def stats(self, interaction: discord.Interaction):
        s = await db.get_settings(interaction.guild_id)
        embed = brand_embed(self.bot, title=f"📊 Stats for {interaction.guild.name}", color=EMBED_COLOR)
        embed.add_field(name="Current Members", value=str(interaction.guild.member_count), inline=True)
        embed.add_field(name="Total Joins (tracked)", value=str(s["total_joins"]), inline=True)
        embed.add_field(name="Total Leaves (tracked)", value=str(s["total_leaves"]), inline=True)
        await interaction.response.send_message(embed=embed)

    # ---------- /ping ----------
    @app_commands.command(name="ping", description="Check the bot's full latency breakdown")
    async def ping(self, interaction: discord.Interaction):
        # Show a quick "beautiful loading" state first.
        await interaction.response.send_message(embed=loading_embed(self.bot, "📡 Pinging everything..."))

        ws_latency_ms = self.bot.latency * 1000  # heartbeat latency, always available

        # API latency: time a real round trip to Discord's REST API.
        api_start = time.perf_counter()
        # A genuine REST round trip: fetch the message we just sent.
        sent_message = await interaction.original_response()
        api_latency_ms = (time.perf_counter() - api_start) * 1000

        # Database latency: time a real round trip to the DB.
        db_start = time.perf_counter()
        await db.get_settings(interaction.guild_id)
        db_latency_ms = (time.perf_counter() - db_start) * 1000

        total_ms = ws_latency_ms + api_latency_ms + db_latency_ms

        embed = brand_embed(
            self.bot,
            title="🏓 Pong!",
            color=EMBED_COLOR,
        )
        embed.add_field(name="🔌 Websocket", value=f"`{ws_latency_ms:.5f}ms`", inline=True)
        embed.add_field(name="🌐 Discord API", value=f"`{api_latency_ms:.5f}ms`", inline=True)
        embed.add_field(name="🗄️ Database", value=f"`{db_latency_ms:.5f}ms`", inline=True)
        embed.add_field(name="⏱️ Total", value=f"`{total_ms:.5f}ms`", inline=False)

        await sent_message.edit(embed=embed)

    # ---------- /help ----------
    @app_commands.command(name="help", description="List all commands the welcomer bot provides")
    async def help_cmd(self, interaction: discord.Interaction):
        embed = brand_embed(
            self.bot,
            title="🤖 Welcomer Bot — Commands",
            description=(
                "New here? Run **`/autosetup`** to configure everything in one step.\n"
                "All configuration commands below require **Manage Server** unless noted otherwise."
            ),
            color=EMBED_COLOR,
        )
        embed.add_field(
            name="🛠️ Quick Start",
            value="`/autosetup` — guided one-command setup for welcome, leave & DM messages",
            inline=False,
        )
        embed.add_field(
            name="👋 Welcome",
            value=(
                "`/welcome channel` — set welcome channel\n"
                "`/welcome toggle` — enable/disable\n"
                "`/welcome message` — set message text\n"
                "`/welcome reset` — restore default message\n"
                "`/welcome card` — toggle image card\n"
                "`/welcome background` — set card background image\n"
                "`/welcome dm` — configure DM welcome\n"
                "`/welcome dm-reset` — restore default DM text\n"
                "`/welcome test` — preview (anyone)\n"
                "`/welcome placeholders` — list variables (anyone)"
            ),
            inline=False,
        )
        embed.add_field(
            name="🚪 Leave",
            value=(
                "`/leave channel` — set leave channel\n"
                "`/leave toggle` — enable/disable\n"
                "`/leave message` — set message text\n"
                "`/leave reset` — restore default message\n"
                "`/leave test` — preview (anyone)"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎭 Autorole",
            value=(
                "`/autorole add` — add a join role\n"
                "`/autorole remove` — remove a join role\n"
                "`/autorole list` — list join roles (anyone)\n"
                "`/autorole clear` — remove all"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️ General",
            value=(
                "`/config` — view full configuration\n"
                "`/embed-color` — set embed color\n"
                "`/stats` — join/leave stats (anyone)\n"
                "`/ping` — latency check (anyone)"
            ),
            inline=False,
        )
        # Not ephemeral: everyone in the channel should be able to see the command list.
        await interaction.response.send_message(embed=embed)

    # ---------- error handling for this cog's app commands ----------
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            embed = brand_embed(
                self.bot,
                title="❌ Missing permission",
                description="You need **Manage Server** permission to do this.",
                color=ERROR_COLOR,
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
