"""
Shared embed builder so every command replies with the same consistent,
"branded" look: bot avatar in the footer, timestamp, consistent color.
"""
import discord
from config import EMBED_COLOR


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
