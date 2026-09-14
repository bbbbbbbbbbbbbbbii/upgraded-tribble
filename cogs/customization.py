"""
- bprefix: a per-server custom text prefix (in addition to mentioning the bot,
  which always works everywhere regardless of this setting).
- bpfp / bbanner / bbio: change the bot's own global Discord identity
  (avatar / profile banner / application description). These affect EVERY
  server the bot is in at once, so they're owner-only.

Notes on bbanner/bbio: bot accounts can set a profile banner via the API
without needing Nitro, but rendering can lag a bit on Discord's end after a
successful edit. bbio edits the application's public description via a REST
endpoint discord.py doesn't wrap with a dedicated method, so it's called
directly — if it errors, paste the traceback and it can be adjusted.
"""
import aiohttp
import discord
from discord.http import Route
from discord.ext import commands

from database import db
from config import SUCCESS_COLOR, ERROR_COLOR
from utils.embeds import brand_embed, loading_embed


async def _download(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


class Customization(commands.Cog, name="Customization"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- per-server prefix ----------
    @commands.command(name="bprefix")
    @commands.has_permissions(manage_guild=True)
    async def bprefix(self, ctx: commands.Context, *, new_prefix: str = None):
        if new_prefix is None or new_prefix.lower() == "reset":
            await db.update(ctx.guild.id, custom_prefix=None)
            embed = brand_embed(self.bot, description="✅ Custom prefix cleared — mentioning me still always works.", color=SUCCESS_COLOR)
            return await ctx.reply(embed=embed, mention_author=False)
        if len(new_prefix) > 10:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Keep the prefix short (10 characters max).", color=ERROR_COLOR), mention_author=False)
        await db.update(ctx.guild.id, custom_prefix=new_prefix)
        embed = brand_embed(self.bot, description=f"✅ Prefix set to `{new_prefix}` for this server (mentioning me still always works too).", color=SUCCESS_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    # ---------- global bot identity (owner only) ----------
    async def _owner_only(self, ctx: commands.Context) -> bool:
        if not await self.bot.is_owner(ctx.author):
            await ctx.reply(embed=brand_embed(self.bot, description="❌ Only the bot owner can change my global appearance.", color=ERROR_COLOR), mention_author=False)
            return False
        return True

    @commands.command(name="bpfp", aliases=["setavatar"])
    async def bpfp(self, ctx: commands.Context, image_url: str = None):
        if not await self._owner_only(ctx):
            return
        image_url = image_url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not image_url:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Give an image URL or attach an image.", color=ERROR_COLOR), mention_author=False)
        status = await ctx.reply(embed=loading_embed(self.bot, "🖼️ Updating my avatar..."), mention_author=False)
        try:
            data = await _download(image_url)
            await self.bot.user.edit(avatar=data)
        except discord.HTTPException as e:
            return await status.edit(embed=brand_embed(self.bot, description=f"❌ Discord rejected that (avatar changes are rate-limited to a couple per hour):\n```{e}```", color=ERROR_COLOR))
        except Exception as e:
            return await status.edit(embed=brand_embed(self.bot, description=f"❌ Couldn't fetch/set that image:\n```{e}```", color=ERROR_COLOR))
        await status.edit(embed=brand_embed(self.bot, description="✅ Avatar updated everywhere.", color=SUCCESS_COLOR))

    @commands.command(name="bbanner", aliases=["setbanner"])
    async def bbanner(self, ctx: commands.Context, image_url: str = None):
        if not await self._owner_only(ctx):
            return
        image_url = image_url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not image_url:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Give an image URL or attach an image.", color=ERROR_COLOR), mention_author=False)
        status = await ctx.reply(embed=loading_embed(self.bot, "🎨 Updating my banner..."), mention_author=False)
        try:
            data = await _download(image_url)
            await self.bot.user.edit(banner=data)
        except discord.HTTPException as e:
            return await status.edit(embed=brand_embed(self.bot, description=f"❌ Discord rejected that:\n```{e}```", color=ERROR_COLOR))
        except Exception as e:
            return await status.edit(embed=brand_embed(self.bot, description=f"❌ Couldn't fetch/set that image:\n```{e}```", color=ERROR_COLOR))
        await status.edit(embed=brand_embed(self.bot, description="✅ Banner updated everywhere.", color=SUCCESS_COLOR))

    @commands.command(name="bbio", aliases=["setbio", "setdescription"])
    async def bbio(self, ctx: commands.Context, *, text: str):
        if not await self._owner_only(ctx):
            return
        if len(text) > 400:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Keep it under 400 characters.", color=ERROR_COLOR), mention_author=False)
        status = await ctx.reply(embed=loading_embed(self.bot, "📝 Updating my bio..."), mention_author=False)
        try:
            route = Route("PATCH", "/applications/@me")
            await self.bot.http.request(route, json={"description": text})
        except Exception as e:
            return await status.edit(embed=brand_embed(self.bot, description=f"❌ Couldn't update the bio:\n```{e}```", color=ERROR_COLOR))
        await status.edit(embed=brand_embed(self.bot, description="✅ Bio updated (may take a minute to show on my profile).", color=SUCCESS_COLOR))


async def setup(bot: commands.Bot):
    await bot.add_cog(Customization(bot))
