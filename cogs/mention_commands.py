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
from config import (
    SUCCESS_COLOR,
    ERROR_COLOR,
    EMBED_COLOR,
    PLACEHOLDERS_HELP,
    DEFAULT_WELCOME_MESSAGE,
    DEFAULT_LEAVE_MESSAGE,
    DEFAULT_DM_MESSAGE,
)
from utils.embeds import brand_embed, loading_embed
from utils.help_menu import build_overview_embed, HelpView
from utils.placeholders import render
from utils.image_gen import generate_card
from cogs.general import run_ping_measurement
from cogs.autorole import perform_autorole_add
from cogs.voicemaster import perform_voicemaster_setup, run_step_sequence


def _hex_to_rgb(color) -> tuple[int, int, int]:
    value = color.value if isinstance(color, discord.Colour) else int(color)
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


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
        await ctx.reply(embed=build_overview_embed(self.bot), view=HelpView(self.bot), mention_author=False)

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

    # ---------- VoiceMaster setup (mirrors /voicemaster setup) ----------
    @commands.group(name="voicemaster", aliases=["vmsetup", "botsetup"], invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def voicemaster(self, ctx: commands.Context):
        """`@Bot voicemaster setup` / `@Bot botsetup voice` — set up Join-to-Create voice channels."""
        await ctx.reply(
            embed=brand_embed(
                self.bot,
                description=f"Use `@{self.bot.user.name} voicemaster setup` to configure Join-to-Create voice channels.",
            ),
            mention_author=False,
        )

    @voicemaster.command(name="setup")
    @commands.has_permissions(manage_guild=True)
    async def voicemaster_setup(self, ctx: commands.Context):
        if not ctx.guild.me.guild_permissions.manage_channels:
            embed = brand_embed(self.bot, description="❌ I need **Manage Channels** permission for this.", color=ERROR_COLOR)
            return await ctx.reply(embed=embed, mention_author=False)

        msg = await ctx.reply(embed=loading_embed(self.bot, "⚡ Booting up..."), mention_author=False)
        steps = [
            "Initializing VoiceMaster Core...",
            "Creating Voice Channels category...",
            "Creating Join-to-Create channel...",
            "Deploying the control panel...",
            "Finalizing configuration...",
        ]
        await run_step_sequence(msg, self.bot, "Setting Up VoiceMaster", steps, emoji="🔊")
        join_channel, panel_channel = await perform_voicemaster_setup(self.bot, ctx.guild)
        done = brand_embed(
            self.bot,
            title="✅ VoiceMaster is set up!",
            description=(
                f"**Join to create:** {join_channel.mention} — join it to get your own temp voice channel\n"
                f"**Control panel:** {panel_channel.mention}"
            ),
            color=SUCCESS_COLOR,
        )
        await msg.edit(embed=done)

    @voicemaster.error
    @voicemaster_setup.error
    async def voicemaster_perm_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            embed = brand_embed(self.bot, title="❌ Missing permission", description="You need **Manage Server** permission to do this.", color=ERROR_COLOR)
            await ctx.reply(embed=embed, mention_author=False)
        else:
            raise error

    # ---------- General info commands ----------
    @commands.command(name="userinfo", aliases=["ui", "whois", "profile"])
    async def userinfo(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        embed = brand_embed(self.bot, title=f"👤 {member.display_name}")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Username", value=str(member), inline=True)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Bot?", value="Yes" if member.bot else "No", inline=True)
        embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "Unknown", inline=True)
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, style="R"), inline=True)
        embed.add_field(name=f"Roles [{len(roles)}]", value=(" ".join(roles) if roles else "None")[:1024], inline=False)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="serverinfo", aliases=["si", "guildinfo"])
    async def serverinfo(self, ctx: commands.Context):
        g = ctx.guild
        embed = brand_embed(self.bot, title=f"🏠 {g.name}")
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="Owner", value=str(g.owner) if g.owner else "Unknown", inline=True)
        embed.add_field(name="Members", value=str(g.member_count), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(g.created_at, style="R"), inline=True)
        embed.add_field(name="Text channels", value=str(len(g.text_channels)), inline=True)
        embed.add_field(name="Voice channels", value=str(len(g.voice_channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
        embed.add_field(name="Boost level", value=f"Level {g.premium_tier} ({g.premium_subscription_count} boosts)", inline=True)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="avatar", aliases=["av", "pfp"])
    async def avatar(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        embed = brand_embed(self.bot, title=f"🖼️ {member.display_name}'s avatar")
        embed.set_image(url=member.display_avatar.url)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="banner")
    async def banner(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        user = await self.bot.fetch_user(member.id)  # banners aren't cached on Member/User by default
        if not user.banner:
            embed = brand_embed(self.bot, description=f"{member.mention} doesn't have a profile banner set.", color=ERROR_COLOR)
            return await ctx.reply(embed=embed, mention_author=False)
        embed = brand_embed(self.bot, title=f"🎨 {member.display_name}'s banner")
        embed.set_image(url=user.banner.url)
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(MentionCommands(bot))
    await bot.add_cog(FullMentionCommands(bot))

# Additional mention-command compatibility layer. Slash commands are intentionally
# left untouched; these commands only provide a message-command entry point.
class FullMentionCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    def _ok(self, ctx, text): return ctx.reply(embed=brand_embed(self.bot, description=text, color=SUCCESS_COLOR), mention_author=False)
    async def _manage(self, ctx):
        if not ctx.author.guild_permissions.manage_guild:
            raise commands.MissingPermissions(['manage_guild'])

    @commands.group(name='welcome', invoke_without_command=True)
    async def welcome(self, ctx):
        await self._ok(ctx, f'Welcome commands: channel, toggle, message, reset, card, background, dm, dm-reset, test, placeholders — e.g. `@{self.bot.user.name} welcome message <text>`')

    @welcome.command(name='channel')
    @commands.has_permissions(manage_guild=True)
    async def welcome_channel(self, ctx, channel: discord.TextChannel):
        await db.update(ctx.guild.id, welcome_channel_id=channel.id, welcome_enabled=1)
        await self._ok(ctx, f'✅ Welcome messages will be sent in {channel.mention}.')

    @welcome.command(name='toggle')
    @commands.has_permissions(manage_guild=True)
    async def welcome_toggle(self, ctx, state: str):
        enabled = 1 if state.lower() == 'on' else 0
        await db.update(ctx.guild.id, welcome_enabled=enabled)
        await self._ok(ctx, f"✅ Welcome messages are now **{'enabled' if enabled else 'disabled'}**.")

    @welcome.command(name='message')
    @commands.has_permissions(manage_guild=True)
    async def welcome_message(self, ctx, *, text: str):
        await db.update(ctx.guild.id, welcome_message=text)
        await self._ok(ctx, '✅ Welcome message updated.')

    @welcome.command(name='reset')
    @commands.has_permissions(manage_guild=True)
    async def welcome_reset(self, ctx):
        await db.update(ctx.guild.id, welcome_message=DEFAULT_WELCOME_MESSAGE)
        await self._ok(ctx, '✅ Welcome message reset to the default.')

    @welcome.command(name='card')
    @commands.has_permissions(manage_guild=True)
    async def welcome_card(self, ctx, state: str):
        enabled = 1 if state.lower() == 'on' else 0
        await db.update(ctx.guild.id, welcome_card=enabled)
        await self._ok(ctx, f"✅ Welcome image card is now **{'enabled' if enabled else 'disabled'}**.")

    @welcome.command(name='background')
    @commands.has_permissions(manage_guild=True)
    async def welcome_background(self, ctx, *, url: str):
        value = None if url.lower() == 'reset' else url
        await db.update(ctx.guild.id, welcome_bg_url=value)
        await self._ok(ctx, '✅ Background updated.' if value else '✅ Background reset to default gradient.')

    @welcome.command(name='dm')
    @commands.has_permissions(manage_guild=True)
    async def welcome_dm(self, ctx, state: str, *, text: str = None):
        fields = {"dm_enabled": 1 if state.lower() == 'on' else 0}
        if text:
            fields["dm_message"] = text
        await db.update(ctx.guild.id, **fields)
        await self._ok(ctx, f"✅ Welcome DM is now **{'enabled' if fields['dm_enabled'] else 'disabled'}**.")

    @welcome.command(name='dm-reset')
    @commands.has_permissions(manage_guild=True)
    async def welcome_dm_reset(self, ctx):
        await db.update(ctx.guild.id, dm_message=DEFAULT_DM_MESSAGE)
        await self._ok(ctx, '✅ Welcome DM message reset to the default.')

    @welcome.command(name='test')
    async def welcome_test(self, ctx):
        settings = await db.get_settings(ctx.guild.id)
        member = ctx.author
        text = render(settings["welcome_message"], member)
        color = settings["embed_color"] or EMBED_COLOR
        embed = brand_embed(self.bot, description=text, color=color, footer=f"Member #{ctx.guild.member_count} • Test preview")
        file = None
        if settings["welcome_card"]:
            try:
                buf = await generate_card(
                    username=member.display_name,
                    subtitle=f"Member #{ctx.guild.member_count} of {ctx.guild.name}",
                    avatar_url=member.display_avatar.replace(size=256).url,
                    background_url=settings["welcome_bg_url"],
                    accent_rgb=_hex_to_rgb(color),
                )
                file = discord.File(buf, filename="welcome.png")
                embed.set_image(url="attachment://welcome.png")
            except Exception:
                file = None
        if file:
            await ctx.reply(embed=embed, file=file, mention_author=False)
        else:
            await ctx.reply(embed=embed, mention_author=False)

    @welcome.command(name='placeholders')
    async def welcome_placeholders(self, ctx):
        await ctx.reply(embed=brand_embed(self.bot, title="📋 Available Placeholders", description=PLACEHOLDERS_HELP, color=EMBED_COLOR), mention_author=False)

    @commands.group(name='leave', invoke_without_command=True)
    async def leave(self, ctx):
        await self._ok(ctx, f'Leave commands: channel, toggle, message, reset, test — e.g. `@{self.bot.user.name} leave message <text>`')

    @leave.command(name='channel')
    @commands.has_permissions(manage_guild=True)
    async def leave_channel(self, ctx, channel: discord.TextChannel):
        await db.update(ctx.guild.id, leave_channel_id=channel.id, leave_enabled=1)
        await self._ok(ctx, f'✅ Leave messages will be sent in {channel.mention}.')

    @leave.command(name='toggle')
    @commands.has_permissions(manage_guild=True)
    async def leave_toggle(self, ctx, state: str):
        await db.update(ctx.guild.id, leave_enabled=1 if state.lower() == 'on' else 0)
        await self._ok(ctx, '✅ Leave setting updated.')

    @leave.command(name='message')
    @commands.has_permissions(manage_guild=True)
    async def leave_message(self, ctx, *, text: str):
        await db.update(ctx.guild.id, leave_message=text)
        await self._ok(ctx, '✅ Leave message updated.')

    @leave.command(name='reset')
    @commands.has_permissions(manage_guild=True)
    async def leave_reset(self, ctx):
        await db.update(ctx.guild.id, leave_message=DEFAULT_LEAVE_MESSAGE)
        await self._ok(ctx, '✅ Leave message reset to the default.')

    @leave.command(name='test')
    async def leave_test(self, ctx):
        settings = await db.get_settings(ctx.guild.id)
        text = render(settings["leave_message"], ctx.author)
        color = settings["embed_color"] or EMBED_COLOR
        embed = brand_embed(self.bot, description=text, color=color, footer="Test preview")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.group(name='automod', invoke_without_command=True)
    async def automod(self, ctx):
        await self._ok(ctx, f'AutoMod commands: status, antispam, antilink, antitoken, anticaps, capslength, mutetime, logchannel — e.g. `@{self.bot.user.name} automod anticaps on`')

    @automod.command(name='status')
    async def automod_status(self, ctx):
        s = await db.get_automod(ctx.guild.id)
        def flag(v):
            return "✅ On" if v else "❌ Off"
        desc = (
            f"**Anti-Link:** {flag(s['antilink'])}\n"
            f"**Anti-Spam:** {flag(s['antispam'])}\n"
            f"**Anti-Token:** {flag(s['antitoken'])}\n"
            f"**Anti-Caps:** {flag(s['anticaps'])} (triggers at `{s['anticaps_min_len']}+` letter ALL-CAPS words)\n"
            f"**Timeout duration:** `{s['mute_seconds']}s`"
        )
        await ctx.reply(embed=brand_embed(self.bot, title='🛡️ AutoMod Status', description=desc, color=EMBED_COLOR), mention_author=False)

    @automod.command(name='antilink')
    @commands.has_permissions(manage_guild=True)
    async def automod_antilink(self, ctx, state: str):
        await db.update_automod(ctx.guild.id, antilink=1 if state.lower() == 'on' else 0)
        await self._ok(ctx, f"🔗 Anti-Link is now **{state.lower()}**.")

    @automod.command(name='antispam')
    @commands.has_permissions(manage_guild=True)
    async def automod_antispam(self, ctx, state: str):
        await db.update_automod(ctx.guild.id, antispam=1 if state.lower() == 'on' else 0)
        await self._ok(ctx, f"⏱️ Anti-Spam is now **{state.lower()}**.")

    @automod.command(name='antitoken')
    @commands.has_permissions(manage_guild=True)
    async def automod_antitoken(self, ctx, state: str):
        await db.update_automod(ctx.guild.id, antitoken=1 if state.lower() == 'on' else 0)
        await self._ok(ctx, f"🔑 Anti-Token is now **{state.lower()}**.")

    @automod.command(name='anticaps')
    @commands.has_permissions(manage_guild=True)
    async def automod_anticaps(self, ctx, state: str):
        await db.update_automod(ctx.guild.id, anticaps=1 if state.lower() == 'on' else 0)
        await self._ok(ctx, f"🔠 Anti-Caps is now **{state.lower()}**.")

    @automod.command(name='capslength')
    @commands.has_permissions(manage_guild=True)
    async def automod_capslength(self, ctx, length: int):
        length = max(2, min(length, 50))
        await db.update_automod(ctx.guild.id, anticaps_min_len=length)
        await self._ok(ctx, f"🔠 Anti-Caps now triggers on ALL-CAPS words of `{length}+` letters.")

    @automod.command(name='mutetime')
    @commands.has_permissions(manage_guild=True)
    async def automod_mutetime(self, ctx, seconds: int):
        await db.update_automod(ctx.guild.id, mute_seconds=seconds)
        await self._ok(ctx, f'✅ AutoMod timeout set to {seconds} seconds.')

    @automod.command(name='logchannel')
    @commands.has_permissions(manage_guild=True)
    async def automod_logchannel(self, ctx, channel: discord.TextChannel):
        await db.update_automod(ctx.guild.id, log_channel_id=channel.id)
        await self._ok(ctx, f'📝 AutoMod actions will be logged in {channel.mention}.')
