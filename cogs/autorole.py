import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import EMBED_COLOR, SUCCESS_COLOR, ERROR_COLOR
from utils.embeds import brand_embed

# Permissions that would let a brand-new member seize control of (or damage) the
# server the moment they join — never allowed on an autorole.
DANGEROUS_PERMS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "manage_messages",
    "manage_nicknames",
    "manage_expressions",
    "kick_members",
    "ban_members",
    "moderate_members",
    "mention_everyone",
)


def _dangerous_perms_on(role: discord.Role) -> list[str]:
    perms = role.permissions
    return [p.replace("_", " ") for p in DANGEROUS_PERMS if getattr(perms, p, False)]


class Autorole(commands.Cog):
    """Automatically assigns one or more roles to members when they join."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    autorole_group = app_commands.Group(name="autorole", description="Configure roles auto-assigned on join")

    @autorole_group.command(name="add", description="Add a role to auto-assign to new members")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def add(self, interaction: discord.Interaction, role: discord.Role):
        if role >= interaction.guild.me.top_role:
            embed = brand_embed(
                self.bot,
                title="❌ Can't use that role",
                description="That role is higher than or equal to my top role. "
                            "Move my role above it in Server Settings → Roles.",
                color=ERROR_COLOR,
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        dangerous = _dangerous_perms_on(role)
        if dangerous:
            embed = brand_embed(
                self.bot,
                title="🛑 Refused — that role is too dangerous to auto-assign",
                description=(
                    f"{role.mention} has **{', '.join(dangerous)}**.\n"
                    "Giving that to every new member on join is a serious security risk "
                    "(raid/nuke potential), so I won't set it as an autorole.\n\n"
                    "Create a separate, low-permission role for autorole instead."
                ),
                color=ERROR_COLOR,
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await db.add_autorole(interaction.guild_id, role.id)
        embed = brand_embed(
            self.bot, description=f"✅ {role.mention} will now be given to new members.", color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autorole_group.command(name="remove", description="Remove a role from the auto-assign list")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        await db.remove_autorole(interaction.guild_id, role.id)
        embed = brand_embed(self.bot, description=f"✅ {role.mention} removed from autorole.", color=SUCCESS_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autorole_group.command(name="list", description="List roles currently auto-assigned on join")
    async def list_roles(self, interaction: discord.Interaction):
        settings = await db.get_settings(interaction.guild_id)
        role_ids = db.role_ids(settings)
        if not role_ids:
            desc = "No autoroles configured. Use `/autorole add` to set one."
        else:
            roles = [interaction.guild.get_role(r) for r in role_ids]
            desc = "\n".join(f"• {r.mention}" for r in roles if r) or "No valid roles found."
        embed = brand_embed(self.bot, title="🎭 Autoroles", description=desc, color=EMBED_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autorole_group.command(name="clear", description="Remove all autoroles")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def clear(self, interaction: discord.Interaction):
        await db.update(interaction.guild_id, autorole_ids="")
        embed = brand_embed(self.bot, description="✅ All autoroles cleared.", color=SUCCESS_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            embed = brand_embed(
                self.bot,
                title="❌ Missing permission",
                description="You need **Manage Roles** permission to use this.",
                color=ERROR_COLOR,
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Autorole(bot))
