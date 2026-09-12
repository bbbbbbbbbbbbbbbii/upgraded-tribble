import discord
from discord import app_commands
from discord.ext import commands

from database import db
from config import (
    SUCCESS_COLOR,
    ERROR_COLOR,
    INFO_COLOR,
    EMBED_COLOR,
    AUTOSETUP_WELCOME_MESSAGE,
    AUTOSETUP_LEAVE_MESSAGE,
    AUTOSETUP_DM_MESSAGE,
    WELCOMER_CATEGORY_NAME,
    WELCOME_CHANNEL_NAME,
    LEAVE_CHANNEL_NAME,
)
from utils.embeds import brand_embed
from utils.placeholders import render
from utils.image_gen import generate_card


class AutosetupConfirmView(discord.ui.View):
    """Confirm / cancel buttons shown before autosetup touches any channels."""

    def __init__(self, cog: "Autosetup", *, author_id: int, plan: dict):
        super().__init__(timeout=60)
        self.cog = cog
        self.author_id = author_id
        self.plan = plan
        self.message: discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who ran `/autosetup` can respond to this.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                embed = brand_embed(
                    self.cog.bot,
                    title="⏳ Setup timed out",
                    description="No response received — run `/autosetup` again when you're ready.",
                    color=ERROR_COLOR,
                )
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Confirm Setup", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await self.cog.run_setup(interaction, self.plan)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        embed = brand_embed(
            self.cog.bot,
            title="✖️ Setup cancelled",
            description="Nothing was changed. Run `/autosetup` again any time.",
            color=ERROR_COLOR,
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class Autosetup(commands.Cog):
    """One command to fully configure welcome, leave, and DM messages."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="autosetup",
        description="Automatically configure welcome, leave, and DM messages in one step",
    )
    @app_commands.describe(
        welcome_channel="Use this channel for welcomes (created automatically if omitted)",
        leave_channel="Use this channel for leave logs (created automatically if omitted)",
        dm_welcome="Also DM new members a welcome message (default: on)",
        autorole="Optional role to auto-assign to new members",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autosetup(
        self,
        interaction: discord.Interaction,
        welcome_channel: discord.TextChannel = None,
        leave_channel: discord.TextChannel = None,
        dm_welcome: bool = True,
        autorole: discord.Role = None,
    ):
        guild = interaction.guild

        if autorole is not None and autorole >= guild.me.top_role:
            embed = brand_embed(
                self.bot,
                title="❌ Can't use that role",
                description=(
                    f"{autorole.mention} is higher than or equal to my top role, so I can't assign it. "
                    "Move my role above it in **Server Settings → Roles** and try again."
                ),
                color=ERROR_COLOR,
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        will_create_welcome = welcome_channel is None and not discord.utils.get(
            guild.text_channels, name=WELCOME_CHANNEL_NAME
        )
        will_create_leave = leave_channel is None and not discord.utils.get(
            guild.text_channels, name=LEAVE_CHANNEL_NAME
        )

        needs_manage_channels = will_create_welcome or will_create_leave
        if needs_manage_channels and not guild.me.guild_permissions.manage_channels:
            embed = brand_embed(
                self.bot,
                title="❌ Missing permission",
                description=(
                    "I need the **Manage Channels** permission to create the welcome/leave channels "
                    "automatically. Either grant me that permission, or re-run `/autosetup` and pass "
                    "`welcome_channel` / `leave_channel` explicitly to reuse existing channels."
                ),
                color=ERROR_COLOR,
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        plan = {
            "welcome_channel": welcome_channel,
            "leave_channel": leave_channel,
            "will_create_welcome": will_create_welcome,
            "will_create_leave": will_create_leave,
            "dm_welcome": dm_welcome,
            "autorole": autorole,
        }

        lines = []
        if welcome_channel:
            lines.append(f"**Welcome channel:** {welcome_channel.mention} *(existing, will be reused)*")
        elif will_create_welcome:
            lines.append(f"**Welcome channel:** will create `#{WELCOME_CHANNEL_NAME}`")
        else:
            existing = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL_NAME)
            lines.append(f"**Welcome channel:** {existing.mention} *(found existing, will be reused)*")

        if leave_channel:
            lines.append(f"**Leave channel:** {leave_channel.mention} *(existing, will be reused)*")
        elif will_create_leave:
            lines.append(f"**Leave channel:** will create `#{LEAVE_CHANNEL_NAME}`")
        else:
            existing = discord.utils.get(guild.text_channels, name=LEAVE_CHANNEL_NAME)
            lines.append(f"**Leave channel:** {existing.mention} *(found existing, will be reused)*")

        lines.append(f"**DM welcome:** {'✅ enabled' if dm_welcome else '❌ disabled'}")
        lines.append(f"**Autorole:** {autorole.mention if autorole else '*none*'}")
        lines.append("**Welcome image card:** ✅ enabled")
        lines.append(
            "\nMessages will be set to polished, ready-to-use defaults — fully editable later with "
            "`/welcome message`, `/leave message`, etc."
        )

        embed = brand_embed(
            self.bot,
            title="🛠️ Autosetup — review before applying",
            description="\n".join(lines),
            color=INFO_COLOR,
        )
        view = AutosetupConfirmView(self, author_id=interaction.user.id, plan=plan)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    async def run_setup(self, interaction: discord.Interaction, plan: dict):
        guild = interaction.guild
        await interaction.followup.send(
            embed=brand_embed(self.bot, description="⚙️ Setting things up..."), ephemeral=True
        )

        try:
            welcome_channel = plan["welcome_channel"] or discord.utils.get(
                guild.text_channels, name=WELCOME_CHANNEL_NAME
            )
            leave_channel = plan["leave_channel"] or discord.utils.get(
                guild.text_channels, name=LEAVE_CHANNEL_NAME
            )

            category = None
            if plan["will_create_welcome"] or plan["will_create_leave"]:
                category = discord.utils.get(guild.categories, name=WELCOMER_CATEGORY_NAME)
                if category is None:
                    category = await guild.create_category(WELCOMER_CATEGORY_NAME, reason="Welcomer autosetup")

            everyone = guild.default_role
            read_only_overwrites = {
                everyone: discord.PermissionOverwrite(
                    view_channel=True, send_messages=False, read_message_history=True
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, embed_links=True, attach_files=True, manage_channels=True
                ),
            }

            if welcome_channel is None:
                welcome_channel = await guild.create_text_channel(
                    WELCOME_CHANNEL_NAME,
                    category=category,
                    overwrites=read_only_overwrites,
                    reason="Welcomer autosetup",
                    topic="New members are announced here. 👋",
                )
            if leave_channel is None:
                leave_channel = await guild.create_text_channel(
                    LEAVE_CHANNEL_NAME,
                    category=category,
                    overwrites=read_only_overwrites,
                    reason="Welcomer autosetup",
                    topic="Member departures are logged here.",
                )

            await db.update(
                guild.id,
                welcome_channel_id=welcome_channel.id,
                welcome_enabled=1,
                welcome_message=AUTOSETUP_WELCOME_MESSAGE,
                welcome_card=1,
                leave_channel_id=leave_channel.id,
                leave_enabled=1,
                leave_message=AUTOSETUP_LEAVE_MESSAGE,
                dm_enabled=1 if plan["dm_welcome"] else 0,
                dm_message=AUTOSETUP_DM_MESSAGE,
            )
            if plan["autorole"] is not None:
                await db.add_autorole(guild.id, plan["autorole"].id)

        except discord.Forbidden:
            embed = brand_embed(
                self.bot,
                title="❌ Missing permissions",
                description=(
                    "I hit a permissions wall while setting things up. Double-check I have "
                    "**Manage Channels** and try again, or supply existing channels manually."
                ),
                color=ERROR_COLOR,
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            embed = brand_embed(
                self.bot,
                title="❌ Setup failed",
                description=f"Discord returned an error while setting things up: `{e}`",
                color=ERROR_COLOR,
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        summary_lines = [
            f"👋 **Welcome channel:** {welcome_channel.mention}",
            f"📤 **Leave channel:** {leave_channel.mention}",
            f"📩 **DM welcome:** {'enabled' if plan['dm_welcome'] else 'disabled'}",
            f"🎭 **Autorole:** {plan['autorole'].mention if plan['autorole'] else 'none'}",
            "🖼️ **Welcome image card:** enabled",
        ]
        done_embed = brand_embed(
            self.bot,
            title="✅ Welcomer is fully set up!",
            description="\n".join(summary_lines) + (
                "\n\nCustomize anytime with `/welcome message`, `/leave message`, `/welcome background`, "
                "`/embed-color`, and more — run `/help` for the full list."
            ),
            color=SUCCESS_COLOR,
        )
        await interaction.followup.send(embed=done_embed, ephemeral=True)

        # live preview so the admin can see exactly what members will experience
        member = interaction.user
        preview_text = render(AUTOSETUP_WELCOME_MESSAGE, member)
        preview_embed = brand_embed(
            self.bot,
            description=preview_text,
            color=EMBED_COLOR,
            footer=f"Member #{guild.member_count} • Live preview",
        )
        try:
            buf = await generate_card(
                username=member.display_name,
                subtitle=f"Member #{guild.member_count} of {guild.name}",
                avatar_url=member.display_avatar.replace(size=256).url,
                background_url=None,
            )
            file = discord.File(buf, filename="preview.png")
            preview_embed.set_image(url="attachment://preview.png")
            await interaction.followup.send(
                content="Here's a live preview of what a new member would see:",
                embed=preview_embed, file=file, ephemeral=True,
            )
        except Exception:
            await interaction.followup.send(
                content="Here's a live preview of what a new member would see:",
                embed=preview_embed, ephemeral=True,
            )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            embed = brand_embed(
                self.bot,
                title="❌ Missing permission",
                description="You need **Manage Server** permission to run this.",
                color=ERROR_COLOR,
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Autosetup(bot))
