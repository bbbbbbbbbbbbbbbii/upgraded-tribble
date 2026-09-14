"""
Shared embed builder so every command replies with the same consistent,
"branded" look: bot avatar in the footer, timestamp, consistent color.
"""
import asyncio
import random
import discord
from config import EMBED_COLOR, NEON_COLOR

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


def _progress_bar(pct: int, length: int = 15) -> str:
    filled = round(length * pct / 100)
    return "[" + ("█" * filled) + ("░" * (length - filled)) + f"] {pct}%"


def steps_embed(
    bot: discord.Client,
    title: str,
    steps: list[str],
    completed: int,
    *,
    emoji: str = "⚡",
) -> discord.Embed:
    """
    Builds the "🛡️ Enabling X System / ✅ » step... / [████░░] 60%" style
    progress embed (glowing-neon accent color, checklist, progress bar).
    `completed` = how many of `steps` are done so far (the rest show a
    spinner on the current step and a blank box on the ones still queued).
    """
    lines = []
    for i, step in enumerate(steps):
        if i < completed:
            lines.append(f"✅ » **{step}**")
        elif i == completed:
            lines.append(f"🔄 » **{step}**")
        else:
            lines.append(f"⬛ » {step}")
    pct = round((completed / len(steps)) * 100) if steps else 100
    lines.append("")
    lines.append(_progress_bar(pct))

    embed = discord.Embed(
        title=f"{emoji} {title}",
        description="\n".join(lines),
        color=NEON_COLOR,
    )
    icon = bot.user.display_avatar.url if bot.user else None
    embed.set_footer(text="Welcomer", icon_url=icon)
    return embed


async def run_step_sequence(
    message: discord.Message,
    bot: discord.Client,
    title: str,
    steps: list[str],
    *,
    emoji: str = "⚡",
    delay: float = 0.7,
) -> discord.Embed:
    """
    Edits `message` step-by-step through a glowing checklist embed (see
    `steps_embed`) so the user watches each stage tick off live, then
    returns the final, fully-completed embed for one last edit by the caller.
    """
    for completed in range(len(steps) + 1):
        embed = steps_embed(bot, title, steps, completed, emoji=emoji)
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            break
        if completed < len(steps):
            await asyncio.sleep(delay)
    return embed
