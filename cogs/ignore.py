"""
'Ignore' system — channels added here stop responding to @Bot mention-commands
(slash commands are unaffected, since those have their own per-channel
permission model in Discord itself).
"""
import discord
from discord.ext import commands

from database import db
from config import SUCCESS_COLOR, ERROR_COLOR, EMBED_COLOR
from utils.embeds import brand_embed


class Ignore(commands.Cog, name="Ignore"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.group(name="ignore", invoke_without_command=True)
    async def ignore(self, ctx: commands.Context):
        await ctx.invoke(self.ignore_list)

    @ignore.command(name="channel")
    @commands.has_permissions(manage_channels=True)
    async def ignore_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        await db.add_ignored_channel(ctx.guild.id, channel.id)
        embed = brand_embed(self.bot, description=f"🚫 I'll ignore mention-commands in {channel.mention} now.", color=SUCCESS_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    @ignore.command(name="remove")
    @commands.has_permissions(manage_channels=True)
    async def ignore_remove(self, ctx: commands.Context, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        await db.remove_ignored_channel(ctx.guild.id, channel.id)
        embed = brand_embed(self.bot, description=f"✅ I'll respond to mention-commands in {channel.mention} again.", color=SUCCESS_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    @ignore.command(name="list")
    async def ignore_list(self, ctx: commands.Context):
        settings = await db.get_settings(ctx.guild.id)
        ids = db.ignored_channel_ids(settings)
        if not ids:
            desc = "No channels are ignored."
        else:
            channels = [ctx.guild.get_channel(c) for c in ids]
            desc = "\n".join(f"• {c.mention}" for c in channels if c) or "No valid channels found."
        await ctx.reply(embed=brand_embed(self.bot, title="🚫 Ignored Channels", description=desc, color=EMBED_COLOR), mention_author=False)

    @ignore_channel.error
    @ignore_remove.error
    async def ignore_perm_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            embed = brand_embed(self.bot, title="❌ Missing permission", description="You need **Manage Channels** permission to do this.", color=ERROR_COLOR)
            await ctx.reply(embed=embed, mention_author=False)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Ignore(bot))
