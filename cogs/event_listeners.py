import io
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord.utils import format_dt

from classes.discordbot import DiscordBot

# Matches an image file extension optionally followed by query parameters.
IMAGE_PATTERN = re.compile(
    r"\.(png|gif|jpe?g|jfif|heif|svg|webp|avif)(?:\?.*)?$", re.IGNORECASE
)


class EventListeners(commands.Cog, name="events"):
    """
    Handles miscellaneous event-based tasks.

    Currently includes:
    - Restoring/saving roles on user rejoin/leaves
    - Audit logs for user join/leave/message edit/delete actions
    - Auto-reactions for images in certain channels
    """

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.subconfig_data: dict = self.bot.config["cogs"][self.__cog_name__.lower()]
        self.autoreact_channel_ids: list[int] = self.subconfig_data.get(
            "autoreact_channel_ids", []
        )
        self.usrlog_channel_id: int | None = self.bot.config["bot"].get(
            "usrlog_channel_id"
        )
        self.msglog_channel_id: int | None = self.bot.config["bot"].get(
            "msglog_channel_id"
        )

    def _relative_ts(self, dt: datetime) -> str:
        """Returns a Discord relative timestamp for a datetime."""
        # Ensure UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = int(dt.timestamp())
        return f"<t:{ts}:R>"

    def _maybe_attach_content(
        self, content: str, limit: int = 1024, prefix: str = "", suffix: str = ""
    ):
        """
        If content exceeds limit, return (None, file) where file is a discord.File of the content;
        else return (truncated_content, None).
        prefix and suffix are added around truncated content.
        """
        if len(content) <= limit:
            return content, None
        # create text attachment
        bio = io.BytesIO(content.encode("utf-8"))
        filename = f"{prefix or 'content'}.txt"
        file = discord.File(fp=bio, filename=filename)
        # indicate attachment in embed
        placeholder = f"{prefix.capitalize()} too long, see attached {filename}"
        return placeholder, file

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Handles a user joining the server by restoring previous roles."""
        guild = member.guild

        now = datetime.now(timezone.utc)

        # Send audit-log embed
        if self.usrlog_channel_id:
            channel = self.bot.get_channel(self.usrlog_channel_id)
            if channel:
                embed = discord.Embed(
                    title="Member Joined",
                    color=discord.Color.green(),
                    timestamp=now,
                )
                embed.description = f"{member.mention} ({member})"
                # Use relative timestamp for account age
                embed.add_field(
                    name="Account Age", value=self._relative_ts(member.created_at)
                )
                embed.add_field(name="User ID", value=str(member.id), inline=True)
                embed.set_thumbnail(url=member.display_avatar.url)
                await channel.send(embed=embed)

        # Restore roles from the database.
        last_roles = self.bot.db.get_member_last_roles(member.id)
        if not last_roles:
            return

        # Get the bot's member instance to determine role hierarchy.
        bot_member = guild.get_member(self.bot.user.id)
        if not bot_member:
            return

        # Build a list of valid roles from the stored role IDs.
        roles = []
        for role_id in last_roles:
            role = guild.get_role(role_id)
            if role is not None:
                # Only add roles that are below the bot's top role.
                if role.position < bot_member.top_role.position:
                    roles.append(role)
                else:
                    self.bot.log(
                        message=f"Role {role_id} exists but is too high to assign.",
                        name="EventListeners.on_member_join",
                    )

        # If we have any valid roles, try to add them.
        if roles:
            try:
                await member.add_roles(
                    *roles, reason="Restoring previous roles after rejoin"
                )
            except (discord.NotFound, discord.HTTPException):
                self.bot.log(
                    message="Failed to restore roles for user.",
                    name="EventListeners.on_member_join",
                )
            finally:
                # Clean the database record after processing, since it's outdated now.
                self.bot.db.delete_member_last_roles(member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Handles a user leaving the server, saving their roles."""
        # Send audit-log embed
        if self.usrlog_channel_id:
            channel = self.bot.get_channel(self.usrlog_channel_id)
            if channel:
                now = datetime.now(timezone.utc)
                embed = discord.Embed(
                    title="Member Left",
                    color=discord.Color.red(),
                    timestamp=now,
                )
                embed.description = f"{member.mention} ({member})"
                # Membership duration: relative from join time
                if member.joined_at:
                    embed.add_field(
                        name="Membership Duration",
                        value=self._relative_ts(member.joined_at),
                    )
                embed.add_field(name="User ID", value=str(member.id), inline=True)
                embed.set_thumbnail(url=member.display_avatar.url)
                await channel.send(embed=embed)

        roles_to_save = [role.id for role in member.roles if role.id != member.guild.id]
        if roles_to_save:
            self.bot.db.update_member_last_roles(member.id, roles_to_save)
        else:
            self.bot.db.delete_member_last_roles(member.id)

    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        """Logs message edits to the configured channel, attaching large content."""
        if before.author.bot or before.content == after.content:
            return
        if not self.msglog_channel_id:
            return

        ch = self.bot.get_channel(self.msglog_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="Message Edited",
            color=discord.Color.blue(),
            timestamp=now,
        )
        embed.description = (
            f"Message Edited in {before.channel.mention} • "
            f"[Jump to Message]({after.jump_url})"
        )

        # Handle "Before"
        before_text = before.content or "[no text content]"
        before_val, before_file = self._maybe_attach_content(
            before_text, prefix="before"
        )
        embed.add_field(name="Before", value=before_val, inline=False)

        # Handle "After"
        after_text = after.content or "[no text content]"
        after_val, after_file = self._maybe_attach_content(after_text, prefix="after")
        embed.add_field(name="After", value=after_val, inline=False)

        embed.set_footer(
            text=f"User ID: {before.author.id} • {format_dt(now, style='f')}"
        )

        files = []
        if before_file:
            files.append(before_file)
        if after_file:
            files.append(after_file)

        await ch.send(embed=embed, files=files or None)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        """Logs message deletions to the configured channel, attaching large content."""
        if message.author.bot:
            return
        if not self.msglog_channel_id:
            return

        ch = self.bot.get_channel(self.msglog_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="Message Deleted",
            color=discord.Color.red(),
            timestamp=now,
        )
        embed.description = f"Message sent by {message.author.mention} Deleted in {message.channel.mention}"

        content_text = message.content or "[no text content]"
        content_val, content_file = self._maybe_attach_content(
            content_text, prefix="content"
        )
        embed.add_field(name="Content", value=content_val, inline=False)

        embed.set_footer(
            text=(
                f"Author ID: {message.author.id} | Message ID: {message.id} • "
                f"{format_dt(now, style='f')}"
            )
        )

        await ch.send(embed=embed, files=[content_file] if content_file else None)

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message) -> None:
        """Handles auto-reactions for images in certain channels."""
        is_autoreact_channel = msg.channel.id in self.autoreact_channel_ids
        has_image_attachment = any(
            IMAGE_PATTERN.search(att.url) for att in msg.attachments
        )
        if is_autoreact_channel and has_image_attachment:
            await msg.add_reaction("❤️")


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(EventListeners(bot))
