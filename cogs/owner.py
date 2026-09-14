"""
Owner-only bot administration. "Owner" = the Discord account that owns this
bot's application (checked via bot.is_owner, same as discord.py's built-in
owner system) OR anyone added via `admin add` by the real owner.

Deliberately NOT included here: a "premium" subscription system and a
"noprefix" trusted-user system — both need real product/business decisions
(what premium unlocks, how payment is handled, who gets vetted for noprefix)
that only the bot's operator can make; building a fake version would just be
misleading. Ask if you want to design either of those properly.
"""
import discord
from discord.ext import commands

from database import db
from config import SUCCESS_COLOR, ERROR_COLOR, EMBED_COLOR
from utils.embeds import brand_embed, loading_embed


async def is_owner_or_admin(ctx: commands.Context) -> bool:
    if await ctx.bot.is_owner(ctx.author):
        return True
    return await db.is_bot_admin(ctx.author.id)


class Owner(commands.Cog, name="Owner"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        return await is_owner_or_admin(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CheckFailure):
            embed = brand_embed(self.bot, title="❌ Owner-only", description="This command is restricted to the bot owner / bot admins.", color=ERROR_COLOR)
            return await ctx.reply(embed=embed, mention_author=False)
        raise error

    # ---------- admin allowlist (owner only — not delegable to other admins) ----------
    @commands.group(name="admin", invoke_without_command=True)
    async def admin(self, ctx: commands.Context):
        await ctx.invoke(self.admin_list)

    @admin.command(name="add")
    async def admin_add(self, ctx: commands.Context, user: discord.User):
        if not await ctx.bot.is_owner(ctx.author):
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Only the actual bot owner can grant admin access.", color=ERROR_COLOR), mention_author=False)
        await db.add_bot_admin(user.id)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ {user.mention} is now a bot admin.", color=SUCCESS_COLOR), mention_author=False)

    @admin.command(name="remove")
    async def admin_remove(self, ctx: commands.Context, user: discord.User):
        if not await ctx.bot.is_owner(ctx.author):
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Only the actual bot owner can revoke admin access.", color=ERROR_COLOR), mention_author=False)
        await db.remove_bot_admin(user.id)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ {user.mention} is no longer a bot admin.", color=SUCCESS_COLOR), mention_author=False)

    @admin.command(name="list")
    async def admin_list(self, ctx: commands.Context):
        ids = await db.list_bot_admins()
        if not ids:
            desc = "No bot admins added yet — just the owner."
        else:
            users = [await self.bot.fetch_user(i) for i in ids]
            desc = "\n".join(f"• {u.mention} (`{u}`)" for u in users)
        await ctx.reply(embed=brand_embed(self.bot, title="👑 Bot Admins", description=desc, color=EMBED_COLOR), mention_author=False)

    # ---------- utility ----------
    @commands.command(name="reload")
    async def reload(self, ctx: commands.Context, extension: str):
        status = await ctx.reply(embed=loading_embed(self.bot, f"🔄 Reloading `cogs.{extension}`..."), mention_author=False)
        try:
            await self.bot.reload_extension(f"cogs.{extension}")
        except Exception as e:
            return await status.edit(embed=brand_embed(self.bot, description=f"❌ Failed to reload `cogs.{extension}`:\n```{e}```", color=ERROR_COLOR))
        await status.edit(embed=brand_embed(self.bot, description=f"✅ Reloaded `cogs.{extension}`.", color=SUCCESS_COLOR))

    @commands.command(name="slist")
    async def slist(self, ctx: commands.Context):
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)[:20]
        lines = [f"`{g.member_count or '?':>5}` members — **{g.name}** (`{g.id}`)" for g in guilds]
        extra = f"\n...and {len(self.bot.guilds) - 20} more" if len(self.bot.guilds) > 20 else ""
        embed = brand_embed(self.bot, title=f"🌐 In {len(self.bot.guilds)} servers", description="\n".join(lines) + extra, color=EMBED_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="dm")
    async def dm(self, ctx: commands.Context, user: discord.User, *, message: str):
        status = await ctx.reply(embed=loading_embed(self.bot, f"📨 Sending DM to {user}..."), mention_author=False)
        try:
            await user.send(message)
        except discord.Forbidden:
            return await status.edit(embed=brand_embed(self.bot, description=f"❌ Couldn't DM {user.mention} — their DMs are closed to me.", color=ERROR_COLOR))
        await status.edit(embed=brand_embed(self.bot, description=f"✅ Sent.", color=SUCCESS_COLOR))


async def setup(bot: commands.Bot):
    await bot.add_cog(Owner(bot))
