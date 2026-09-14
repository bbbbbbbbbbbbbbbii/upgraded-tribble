"""
Owner-only bot administration. "Owner" = the Discord account(s) that pass
bot.is_owner() — either the Developer Portal application owner (or every
member of its Team), or whoever is listed in OWNER_IDS in .env (that env
var overrides the automatic lookup entirely — see config.py).

Bot admins (`admin add/remove/list`) are a separate, delegable allowlist:
only the real owner can grant/revoke admin, but admins can then use
everything else in this cog (slist, dm, reload, noprefix, premium, ...).

Note on `premium`: this is a tracking/allowlist system only — it records
who's marked premium, nothing more. It doesn't unlock or gate anything by
itself because no premium-only features exist yet. `db.is_premium_user()` /
`db.is_premium_guild()` are there so specific commands can be gated later
once there's an actual premium feature and a real decision about what it
unlocks — building a fake "premium" that pretends to unlock something would
just be misleading, so it deliberately doesn't do that yet.

Note on `noprefix`: granting this lets that user (or every member of that
guild) run commands with zero prefix and no mention at all — e.g. just
typing "ping". That's powerful but also means any ordinary sentence that
happens to start with a command name (typing "help me with something") will
be treated as a command attempt. Grant it only to people who understand
that tradeoff.
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
        if isinstance(error, commands.UserNotFound):
            embed = brand_embed(self.bot, description="❌ Couldn't find that user — try a mention or a raw user ID.", color=ERROR_COLOR)
            return await ctx.reply(embed=embed, mention_author=False)
        if isinstance(error, commands.MissingRequiredArgument):
            embed = brand_embed(self.bot, description=f"❌ Missing something — usage: `{ctx.prefix}{ctx.command} {ctx.command.signature}`", color=ERROR_COLOR)
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

    # ---------- noprefix ----------
    @commands.group(name="noprefix", invoke_without_command=True)
    async def noprefix(self, ctx: commands.Context):
        await ctx.invoke(self.noprefix_list)

    @noprefix.command(name="add")
    async def noprefix_add(self, ctx: commands.Context, user: discord.User):
        await db.add_noprefix_user(user.id)
        embed = brand_embed(self.bot, description=f"✅ {user.mention} can now run commands with no prefix at all (e.g. just `ping`).", color=SUCCESS_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    @noprefix.command(name="remove")
    async def noprefix_remove(self, ctx: commands.Context, user: discord.User):
        await db.remove_noprefix_user(user.id)
        embed = brand_embed(self.bot, description=f"✅ {user.mention} needs a prefix/mention again.", color=SUCCESS_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    @noprefix.command(name="list")
    async def noprefix_list(self, ctx: commands.Context):
        ids = await db.list_noprefix_users()
        if not ids:
            desc = "No users have noprefix."
        else:
            users = [await self.bot.fetch_user(i) for i in ids]
            desc = "\n".join(f"• {u.mention} (`{u}`)" for u in users)
        await ctx.reply(embed=brand_embed(self.bot, title="🔓 Noprefix Users", description=desc, color=EMBED_COLOR), mention_author=False)

    @noprefix.command(name="status")
    async def noprefix_status(self, ctx: commands.Context, user: discord.User = None):
        user = user or ctx.author
        is_np = await db.is_noprefix_user(user.id)
        guild_np = await db.is_noprefix_guild(ctx.guild.id) if ctx.guild else False
        desc = f"{user.mention}: **{'✅ has' if is_np else '❌ does not have'}** personal noprefix."
        if ctx.guild:
            desc += f"\nThis server: **{'✅ noprefix for everyone' if guild_np else '❌ off'}**."
        await ctx.reply(embed=brand_embed(self.bot, title="🔓 Noprefix Status", description=desc, color=EMBED_COLOR), mention_author=False)

    @noprefix.group(name="guild", invoke_without_command=True)
    async def noprefix_guild(self, ctx: commands.Context):
        await ctx.invoke(self.noprefix_guild_list)

    @noprefix_guild.command(name="add")
    async def noprefix_guild_add(self, ctx: commands.Context, guild_id: int = None):
        gid = guild_id or (ctx.guild.id if ctx.guild else None)
        if gid is None:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Give a guild ID (or run this inside the server).", color=ERROR_COLOR), mention_author=False)
        await db.add_noprefix_guild(gid)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Everyone in guild `{gid}` can now run commands with no prefix.", color=SUCCESS_COLOR), mention_author=False)

    @noprefix_guild.command(name="remove")
    async def noprefix_guild_remove(self, ctx: commands.Context, guild_id: int = None):
        gid = guild_id or (ctx.guild.id if ctx.guild else None)
        if gid is None:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Give a guild ID (or run this inside the server).", color=ERROR_COLOR), mention_author=False)
        await db.remove_noprefix_guild(gid)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Guild `{gid}` no longer has server-wide noprefix.", color=SUCCESS_COLOR), mention_author=False)

    @noprefix_guild.command(name="list")
    async def noprefix_guild_list(self, ctx: commands.Context):
        ids = await db.list_noprefix_guilds()
        if not ids:
            desc = "No guilds have server-wide noprefix."
        else:
            lines = []
            for gid in ids:
                g = self.bot.get_guild(gid)
                lines.append(f"• **{g.name}** (`{gid}`)" if g else f"• `{gid}` (not in this guild)")
            desc = "\n".join(lines)
        await ctx.reply(embed=brand_embed(self.bot, title="🔓 Noprefix Guilds", description=desc, color=EMBED_COLOR), mention_author=False)

    # ---------- premium (allowlist/tracking only — see module docstring) ----------
    @commands.group(name="premium", invoke_without_command=True)
    async def premium(self, ctx: commands.Context):
        await ctx.invoke(self.premium_list)

    @premium.command(name="add")
    async def premium_add(self, ctx: commands.Context, user: discord.User):
        await db.add_premium_user(user.id)
        embed = brand_embed(self.bot, description=f"✅ {user.mention} is now marked premium.", color=SUCCESS_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    @premium.command(name="remove")
    async def premium_remove(self, ctx: commands.Context, user: discord.User):
        await db.remove_premium_user(user.id)
        embed = brand_embed(self.bot, description=f"✅ {user.mention} is no longer marked premium.", color=SUCCESS_COLOR)
        await ctx.reply(embed=embed, mention_author=False)

    @premium.command(name="list")
    async def premium_list(self, ctx: commands.Context):
        rows = await db.list_premium_users()
        if not rows:
            desc = "No premium users yet."
        else:
            lines = []
            for row in rows:
                try:
                    u = await self.bot.fetch_user(row["user_id"])
                    lines.append(f"• {u.mention} (`{u}`) — since {row['added_at']}")
                except discord.NotFound:
                    lines.append(f"• `{row['user_id']}` — since {row['added_at']}")
            desc = "\n".join(lines)
        await ctx.reply(embed=brand_embed(self.bot, title="💎 Premium Users", description=desc, color=EMBED_COLOR), mention_author=False)

    @premium.command(name="status")
    async def premium_status(self, ctx: commands.Context, user: discord.User = None):
        user = user or ctx.author
        is_prem = await db.is_premium_user(user.id)
        guild_prem = await db.is_premium_guild(ctx.guild.id) if ctx.guild else False
        desc = f"{user.mention}: **{'💎 premium' if is_prem else '❌ not premium'}**."
        if ctx.guild:
            desc += f"\nThis server: **{'💎 premium' if guild_prem else '❌ not premium'}**."
        await ctx.reply(embed=brand_embed(self.bot, title="💎 Premium Status", description=desc, color=EMBED_COLOR), mention_author=False)

    @premium.group(name="guild", invoke_without_command=True)
    async def premium_guild(self, ctx: commands.Context):
        await ctx.invoke(self.premium_guild_list)

    @premium_guild.command(name="add")
    async def premium_guild_add(self, ctx: commands.Context, guild_id: int = None):
        gid = guild_id or (ctx.guild.id if ctx.guild else None)
        if gid is None:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Give a guild ID (or run this inside the server).", color=ERROR_COLOR), mention_author=False)
        await db.add_premium_guild(gid)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Guild `{gid}` is now marked premium.", color=SUCCESS_COLOR), mention_author=False)

    @premium_guild.command(name="remove")
    async def premium_guild_remove(self, ctx: commands.Context, guild_id: int = None):
        gid = guild_id or (ctx.guild.id if ctx.guild else None)
        if gid is None:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Give a guild ID (or run this inside the server).", color=ERROR_COLOR), mention_author=False)
        await db.remove_premium_guild(gid)
        await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Guild `{gid}` is no longer marked premium.", color=SUCCESS_COLOR), mention_author=False)

    @premium_guild.command(name="list")
    async def premium_guild_list(self, ctx: commands.Context):
        rows = await db.list_premium_guilds()
        if not rows:
            desc = "No premium guilds yet."
        else:
            lines = []
            for row in rows:
                g = self.bot.get_guild(row["guild_id"])
                name = g.name if g else "unknown (not in this guild)"
                lines.append(f"• **{name}** (`{row['guild_id']}`) — since {row['added_at']}")
            desc = "\n".join(lines)
        await ctx.reply(embed=brand_embed(self.bot, title="💎 Premium Guilds", description=desc, color=EMBED_COLOR), mention_author=False)

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
