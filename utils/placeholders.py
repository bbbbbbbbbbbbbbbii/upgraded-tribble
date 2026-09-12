"""
Turns a template string with {placeholders} into the final message
for a given member, safely (unknown placeholders are left as-is instead
of raising).
"""
import discord


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(template: str, member: discord.Member) -> str:
    guild = member.guild
    values = SafeDict(
        mention=member.mention,
        user=str(member),
        user_name=member.display_name,
        server=guild.name,
        membercount=guild.member_count,
        membercount_ordinal=_ordinal(guild.member_count),
    )
    return template.format_map(values)
