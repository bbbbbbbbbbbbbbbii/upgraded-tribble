"""
These are plain discord.py "prefix" commands, which — because the bot's
command_prefix is commands.when_mentioned_or("!wb ") — can be triggered by
mentioning the bot, e.g. "@Welcomer ping", as well as by "!wb ping".

They live alongside (not instead of) the existing /ping, /help, and
/autorole slash commands — same features, another way to trigger them.
"""
import time
import discord
from discord.ext import commands

from database import db
from config import SUCCESS_COLOR, ERROR_COLOR
from utils.embeds import brand_embed, loading_embed
from cogs.general import build_help_embed, run_ping_measurement
from cogs.autorole import perform_autorole_add


class MentionCommands(commands.Cog):
    """@Bot-mention versions of the most common commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        sent = await ctx.reply(embed=loading_embed(self.bot, "📡 Pinging everything..."), mention_author=False)

        async def api_probe():
            start = time.perf_counter()
            await ctx.channel.fetch_message(sent.id)  # a genuine REST round trip
            return time.perf_counter() - start

        embed = await run_ping_measurement(self.bot, ctx.guild.id, api_probe)
        await sent.edit(embed=embed)

    @commands.command(name="help")
    async def help_cmd(self, ctx: commands.Context):
        await ctx.reply(embed=build_help_embed(self.bot, mention_style=True), mention_author=False)

    @commands.group(name="autorole", invoke_without_command=True)
    async def autorole(self, ctx: commands.Context):
        settings = await db.get_settings(ctx.guild.id)
        role_ids = db.role_ids(settings)
        if not role_ids:
            desc = f"No autoroles configured. Use `@{self.bot.user.name} autorole add @role` to set one."
        else:
            roles = [ctx.guild.get_role(r) for r in role_ids]
            desc = "\n".join(f"• {r.mention}" for r in roles if r) or "No valid roles found."
        await ctx.reply(embed=brand_embed(self.bot, title="🎭 Autoroles", description=desc), mention_author=False)

    @autorole.command(name="add")
    @commands.has_permissions(manage_roles=True)
    async def autorole_add(self, ctx: commands.Context, role: discord.Role):
        embed = await perform_autorole_add(self.bot, ctx.guild, role)
        await ctx.reply(embed=embed, mention_author=False)

    @autorole.command(name="remove")
    @commands.has_permissions(manage_roles=True)
    async def autorole_remove(self, ctx: commands.Context, role: discord.Role):
        await db.remove_autorole(ctx.guild.id, role.id)
        embed = brand_embed(self.bot, description=f"✅ {role.mention} removed from autorole.", color=SUCCESS_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    @autorole_add.error
    @autorole_remove.error
    async def autorole_perm_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            embed = brand_embed(
                self.bot, title="❌ Missing permission",
                description="You need **Manage Roles** permission to do this.", color=ERROR_COLOR,
            )
            await ctx.reply(embed=embed, mention_author=False)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(MentionCommands(bot))
