"""
AFK — set yourself AFK with a reason; the bot clears it automatically on your
next message, and gently tells anyone who mentions you that you're away.
"""
import discord
from discord.ext import commands

from database import db
from config import SUCCESS_COLOR, EMBED_COLOR
from utils.embeds import brand_embed


class AFK(commands.Cog, name="AFK"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        await db.set_afk(ctx.guild.id, ctx.author.id, reason[:200])
        embed = brand_embed(self.bot, description=f"💤 {ctx.author.mention} is now AFK: {reason}", color=SUCCESS_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.Cog.listener("on_message")
    async def afk_watcher(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        # Welcome back the returning user, if they had an AFK status.
        entry = await db.get_afk(message.guild.id, message.author.id)
        if entry:
            await db.clear_afk(message.guild.id, message.author.id)
            try:
                await message.channel.send(
                    embed=brand_embed(self.bot, description=f"👋 Welcome back, {message.author.mention} — I removed your AFK.", color=SUCCESS_COLOR),
                    delete_after=8,
                )
            except discord.HTTPException:
                pass

        # Let the author know if they just mentioned someone who's AFK.
        for user in message.mentions:
            if user.id == message.author.id:
                continue
            other = await db.get_afk(message.guild.id, user.id)
            if other:
                try:
                    await message.channel.send(
                        embed=brand_embed(self.bot, description=f"💤 {user.mention} is AFK: {other['reason']}", color=EMBED_COLOR)
                    )
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))
