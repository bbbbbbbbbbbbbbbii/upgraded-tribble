"""
Shared embed builder so every command replies with the same consistent,
"branded" look: bot avatar in the footer, timestamp, consistent color.
"""
import random
import discord
from config import EMBED_COLOR

LOADING_MESSAGES = [
    "✨ Sprinkling some magic...",
    "🛠️ Assembling the response...",
    "🌐 Talking to Discord...",
    "🔧 Configuring things nicely...",
    "💫 Almost there...",
]


def loading_embed(bot: discord.Client, text: str | None = None) -> discord.Embed:
    """A small stylish 'working on it' embed to show while a command finishes up."""
    embed = discord.Embed(
        description=f"{text or random.choice(LOADING_MESSAGES)}",
        color=EMBED_COLOR,
    )
    icon = bot.user.display_avatar.url if bot.user else None
    embed.set_footer(text="Welcomer", icon_url=icon)
    return embed


def brand_embed(
    bot: discord.Client,
    *,
    description: str | None = None,
    title: str | None = None,
    color: int | discord.Colour | None = None,
    footer: str | None = None,
    timestamp: bool = True,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color if color is not None else EMBED_COLOR,
    )
    if timestamp:
        embed.timestamp = discord.utils.utcnow()

    icon = bot.user.display_avatar.url if bot.user else None
    footer_text = footer or "Welcomer"
    embed.set_footer(text=footer_text, icon_url=icon)
    return embed
