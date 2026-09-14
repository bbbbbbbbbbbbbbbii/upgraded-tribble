"""
Reaction roles: react to a message with a chosen emoji to get (or remove) a role.
"""
import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import SUCCESS_COLOR, ERROR_COLOR, EMBED_COLOR
from utils.embeds import brand_embed


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    reactionrole_group = app_commands.Group(
        name="reactionrole", description="React to a message to get a role",
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @reactionrole_group.command(name="add", description="Turn an emoji reaction on a message into a role toggle")
    @app_commands.describe(
        message_id="The ID of the message to attach the reaction role to",
        emoji="The emoji members will react with (unicode or custom server emoji)",
        role="The role to give/remove when reacted",
        channel="Channel the message is in (defaults to this channel)",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def add(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
        role: discord.Role,
        channel: discord.TextChannel = None,
    ):
        channel = channel or interaction.channel
        if role >= interaction.guild.me.top_role:
            embed = brand_embed(self.bot, description="❌ That role is higher than or equal to my top role.", color=ERROR_COLOR)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        try:
            mid = int(message_id)
            message = await channel.fetch_message(mid)
        except (ValueError, discord.NotFound, discord.Forbidden):
            embed = brand_embed(self.bot, description="❌ Couldn't find that message in that channel.", color=ERROR_COLOR)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            embed = brand_embed(self.bot, description="❌ I couldn't react with that emoji — is it a valid emoji I have access to?", color=ERROR_COLOR)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await db.add_reaction_role(interaction.guild_id, message.id, channel.id, str(emoji), role.id)
        embed = brand_embed(
            self.bot,
            description=f"✅ Reacting with {emoji} on [that message]({message.jump_url}) now gives {role.mention}.",
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @reactionrole_group.command(name="list", description="List all reaction roles configured on this server")
    async def list_roles(self, interaction: discord.Interaction):
        rows = await db.list_reaction_roles(interaction.guild_id)
        if not rows:
            desc = "No reaction roles configured. Use `/reactionrole add` to create one."
        else:
            lines = []
            for r in rows:
                role = interaction.guild.get_role(r["role_id"])
                channel = interaction.guild.get_channel(r["channel_id"])
                jump = f"https://discord.com/channels/{interaction.guild_id}/{r['channel_id']}/{r['message_id']}"
                lines.append(f"• {r['emoji']} → {role.mention if role else '@deleted-role'} in {channel.mention if channel else '#deleted-channel'} [(jump)]({jump})")
            desc = "\n".join(lines)
        embed = brand_embed(self.bot, title="🔥 Reaction Roles", description=desc, color=EMBED_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @reactionrole_group.command(name="reset", description="Remove all reaction roles configured on this server")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def reset(self, interaction: discord.Interaction):
        await db.reset_reaction_roles(interaction.guild_id)
        embed = brand_embed(self.bot, description="✅ All reaction roles for this server have been cleared.", color=SUCCESS_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- enforcement ----------
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.member is None or payload.member.bot:
            return
        emoji = str(payload.emoji)
        mapping = await db.get_reaction_role(payload.message_id, emoji)
        if not mapping:
            return
        guild = self.bot.get_guild(payload.guild_id)
        role = guild.get_role(mapping["role_id"]) if guild else None
        if role:
            try:
                await payload.member.add_roles(role, reason="Reaction role")
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return
        emoji = str(payload.emoji)
        mapping = await db.get_reaction_role(payload.message_id, emoji)
        if not mapping:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        role = guild.get_role(mapping["role_id"])
        if role:
            try:
                await member.remove_roles(role, reason="Reaction role removed")
            except discord.HTTPException:
                pass

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            embed = brand_embed(self.bot, title="❌ Missing permission", description="You need **Manage Roles** permission to use this.", color=ERROR_COLOR)
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
