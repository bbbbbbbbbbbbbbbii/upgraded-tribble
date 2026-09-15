"""
Categorized @Bot help / /help menu: an overview page (total commands, prefix,
category list, Invite + Support Server buttons) plus a dropdown that swaps in
each category's command list — same shape as the reference screenshots.
"""
import discord
from discord.ext import commands

from config import EMBED_COLOR, SUPPORT_SERVER_URL, INVITE_PERMISSIONS, HELP_BANNER_URL


def _category_defs(bot: commands.Bot) -> dict:
    m = f"@{bot.user.name}" if bot.user else "@Bot"
    return {
        "quickstart": (
            "🛠️", "Quick Start",
            f"`/autosetup` or `{m} autosetup` — guided one-command setup for welcome, leave & DM messages",
        ),
        "welcome": (
            "👋", "Welcome",
            "`/welcome channel` — set welcome channel\n"
            "`/welcome toggle` — enable/disable\n"
            "`/welcome message` — set message text\n"
            "`/welcome reset` — restore default message\n"
            "`/welcome card` — toggle image card\n"
            "`/welcome background` — set card background image\n"
            "`/welcome dm` — configure DM welcome\n"
            "`/welcome dm-reset` — restore default DM text\n"
            "`/welcome test` — preview (anyone)\n"
            "`/welcome placeholders` — list variables (anyone)",
        ),
        "leave": (
            "🚪", "Leave",
            "`/leave channel` — set leave channel\n"
            "`/leave toggle` — enable/disable\n"
            "`/leave message` — set message text\n"
            "`/leave reset` — restore default message\n"
            "`/leave test` — preview (anyone)",
        ),
        "autorole": (
            "🎭", "Autorole",
            f"`/autorole add` or `{m} autorole add @role` — add a join role\n"
            f"`/autorole remove` or `{m} autorole remove @role` — remove a join role\n"
            f"`{m} autorole` — list join roles (anyone)\n"
            "`/autorole clear` — remove all",
        ),
        "music": (
            "🎵", "Music (mention-only, no slash)",
            f"`{m} play <song>` (`p`) — play/queue a song or playlist\n"
            f"`{m} skip` (`s`) — skip the current song\n"
            f"`{m} stop` — stop and clear the queue\n"
            f"`{m} pause` / `resume` — pause/resume\n"
            f"`{m} loop` — cycle Off → Track → Queue\n"
            f"`{m} shuffle` — shuffle the queue\n"
            f"`{m} autoplay` — toggle auto-queuing similar tracks\n"
            f"`{m} queue` (`q`) — show the queue\n"
            f"`{m} nowplaying` (`np`) — show current song + controls\n"
            f"`{m} join` / `leave` — voice channel control\n"
            f"`{m} 24/7` — toggle staying connected 24/7\n"
            f"`{m} volume <0-150>` — set volume\n"
            "Also fully controllable from the buttons + filter dropdown on the Now Playing card.",
        ),
        "general": (
            "📺", "General",
            f"`{m} ping` (`/ping`) — latency check (anyone)\n"
            f"`{m} userinfo [@user]` (`profile`) — info about a member (anyone)\n"
            f"`{m} serverinfo` — info about this server (anyone)\n"
            f"`{m} avatar [@user]` — a member's avatar (anyone)\n"
            f"`{m} banner [@user]` — a member's profile banner (anyone)\n"
            f"`{m} afk [reason]` — set yourself AFK (anyone)\n"
            "`/config` — view full welcomer configuration\n"
            "`/embed-color` — set embed color\n"
            "`/stats` — join/leave stats (anyone)",
        ),
        "customization": (
            "🖌️", "Customization",
            f"`{m} bprefix <text>` — set a custom prefix for this server (or `reset`)\n"
            f"`{m} bpfp <image url>` — change my avatar (bot owner only)\n"
            f"`{m} bbanner <image url>` — change my profile banner (bot owner only)\n"
            f"`{m} bbio <text>` — change my bio (bot owner only)",
        ),
        "ignore": (
            "🚫", "Ignore Channels",
            f"`{m} ignore channel [#channel]` — stop responding to mention-commands there\n"
            f"`{m} ignore remove [#channel]` — start responding there again\n"
            f"`{m} ignore list` — show ignored channels (anyone)",
        ),
        "owner": (
            "👑", "Owner",
            f"`{m} admin add/remove/list @user` — bot-admin allowlist (owner grants/revokes)\n"
            f"`{m} noprefix add/remove/list/status @user` — let a user run commands with zero prefix\n"
            f"`{m} noprefix guild add/remove/list [guild_id]` — same, for an entire server\n"
            f"`{m} premium add/remove/list/status @user` — mark a user premium (tracking only for now)\n"
            f"`{m} premium guild add/remove/list [guild_id]` — same, for an entire server\n"
            f"`{m} reload <cog name>` — hot-reload a cog\n"
            f"`{m} slist` — list servers I'm in\n"
            f"`{m} dm @user <message>` — DM someone as me\n"
            "*(restricted to the bot owner / bot admins)*",
        ),
    }


def _count_commands(bot: commands.Bot) -> int:
    slash = len(list(bot.tree.walk_commands()))
    prefix = len([c for c in bot.walk_commands() if not c.hidden])
    return slash + prefix


def build_overview_embed(bot: commands.Bot) -> discord.Embed:
    defs = _category_defs(bot)
    name = bot.user.name if bot.user else "Welcomer"
    embed = discord.Embed(
        title=f"{name} — Help",
        description=(
            f"Hey! I'm **{name}**, your welcomer + music bot.\n\n"
            f"**Total Commands:** {_count_commands(bot)}\n"
            f"**Total Servers:** {len(bot.guilds)}\n"
            f"**Prefix:** `/` slash commands, or mention me — `@{name}`\n\n"
            "**Categories**"
        ),
        color=EMBED_COLOR,
    )
    for emoji, title, _ in defs.values():
        embed.add_field(name="\u200b", value=f"{emoji} **{title}**", inline=False)
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    if HELP_BANNER_URL:
        embed.set_image(url=HELP_BANNER_URL)
    embed.set_footer(text="Select a category below to see its commands ⬇️")
    return embed


def build_category_embed(bot: commands.Bot, key: str) -> discord.Embed:
    defs = _category_defs(bot)
    emoji, title, body = defs.get(key, ("❓", "Unknown", "Nothing here."))
    embed = discord.Embed(title=f"{emoji} {title}", description=body, color=EMBED_COLOR)
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        defs = _category_defs(bot)
        options = [
            discord.SelectOption(label=title, value=key, emoji=emoji)
            for key, (emoji, title, _) in defs.items()
        ]
        super().__init__(placeholder="Select a category...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=build_category_embed(self.bot, self.values[0]), view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.add_item(CategorySelect(bot))
        if bot.user:
            self.add_item(discord.ui.Button(
                label="Invite Bot", style=discord.ButtonStyle.link,
                url=f"https://discord.com/oauth2/authorize?client_id={bot.user.id}&permissions={INVITE_PERMISSIONS}&integration_type=0&scope=bot",
            ))
        if SUPPORT_SERVER_URL:
            self.add_item(discord.ui.Button(label="Support Server", style=discord.ButtonStyle.link, url=SUPPORT_SERVER_URL))
