import io
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import discord
from discord.ext import commands

from classes.discordbot import DiscordBot

# Matches an image file extension optionally followed by query parameters
IMAGE_PATTERN = re.compile(
    r"\.(png|gif|jpe?g|jfif|heif|svg|webp|avif)(?:\?.*)?$", re.IGNORECASE
)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".jfif")

# Seconds after a ban where deleted messages won't be logged (avoid rate limits)
IGNORE_WINDOW = 600


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
        self.modlog_channel_id: int | None = self.bot.config["bot"].get(
            "modlog_channel_id"
        )
        # Tracks recent bans to avoid logging deleted messages (user_id -> timestamp)
        self._recent_bans: dict[int, float] = defaultdict(float)

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

    async def _log_simple_event(
        self,
        channel_id: int | None,
        title: str,
        description: str,
        color: discord.Color,
        *,
        fields: list[tuple[str, str, bool]] = None,
        image_url: str = None,
        files: list[discord.File] = None,
    ) -> None:
        """Helper to send a simple embed to a given channel."""
        if not channel_id:
            return
        ch = self.bot.get_channel(channel_id)
        if not isinstance(ch, discord.TextChannel):
            return

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title=title, description=description, color=color, timestamp=now
        )

        # attach any extra fields
        for name, value, inline in fields or []:
            embed.add_field(name=name, value=value, inline=inline)

        # optionally include an image
        if image_url:
            embed.set_image(url=image_url)

        try:
            await ch.send(embed=embed, files=files)
        except discord.Forbidden:
            self.bot.log(
                message=f"Forbidden to send to {channel_id}", name="_log_simple_event"
            )
        except discord.HTTPException as e:
            self.bot.log(
                message=f"HTTP error sending log: {e}", name="_log_simple_event"
            )

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
            f"Message edited in {before.channel.mention} by {before.author.mention}  • "
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

        embed.set_footer(text=f"User ID: {before.author.id}")

        files = []
        if before_file:
            files.append(before_file)
        if after_file:
            files.append(after_file)

        await ch.send(embed=embed, files=files or None)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        # Ignore if the message is from a bot or if the channel is not set
        if not message.author:
            return

        if message.author.bot or not self.msglog_channel_id:
            return

        ch = self.bot.get_channel(self.msglog_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return

        # Ignore if the message is from a user who was recently bannedg
        if (
            message.author.id in self._recent_bans
            and self._recent_bans[message.author.id] > time.time()
        ):
            return

        now = datetime.now(timezone.utc)

        # 1) Log text content deletion, if any
        if message.content:
            embed = discord.Embed(
                title="Message Deleted",
                color=discord.Color.red(),
                timestamp=now,
            )
            embed.description = f"Message sent by {message.author.mention} • Deleted in {message.channel.mention}"
            content_val, content_file = self._maybe_attach_content(
                message.content, prefix="content"
            )
            embed.add_field(name="Content", value=content_val, inline=False)
            embed.set_footer(
                text=(
                    f"Author ID: {message.author.id} | "
                    f"Message ID: {message.id} • {self._relative_ts(now)}"
                )
            )
            await ch.send(embed=embed, files=[content_file] if content_file else None)

        # 2) Log each attachment separately
        for att in message.attachments:
            # Decide title based on whether it's an image or other file
            title = "Image" if att.filename.lower().endswith(IMAGE_EXTS) else "File"
            att_embed = discord.Embed(
                title=title,
                color=discord.Color.orange(),
                timestamp=now,
            )
            att_embed.description = f"{title} sent by {message.author.mention} • Deleted in {message.channel.mention}"
            # If it's an image, show it; otherwise just link the file
            if any(att.filename.lower().endswith(ext) for ext in IMAGE_EXTS):
                att_embed.set_image(url=att.proxy_url)
            else:
                att_embed.add_field(name="Filename", value=att.filename, inline=True)
                att_embed.add_field(name="URL", value=att.url, inline=False)

            att_embed.set_footer(
                text=f"Author ID: {message.author.id} | " f"Message ID: {message.id}"
            )
            await ch.send(embed=att_embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        channel_mention = (
            messages[0].channel.mention if messages else "⚠️ unknown channel"
        )
        await self._log_simple_event(
            self.msglog_channel_id,
            title="Bulk Message Delete",
            description=f"{len(messages)} messages were deleted in {channel_mention}",
            color=discord.Color.dark_red(),
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not self.modlog_channel_id:
            return
        ch = self.bot.get_channel(self.modlog_channel_id)
        minor_ch = self.bot.get_channel(self.msglog_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        now = datetime.now(timezone.utc)

        # Nickname changes
        if before.nick != after.nick:
            embed = discord.Embed(
                title="Nickname Changed", color=discord.Color.orange(), timestamp=now
            )
            embed.description = f"{after.mention}"
            embed.add_field(name="Before", value=before.nick or "[none]", inline=True)
            embed.add_field(name="After", value=after.nick or "[none]", inline=True)
            await minor_ch.send(embed=embed)

        # Role adds/removes
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        for role in added:
            embed = discord.Embed(
                title="Role Added", color=discord.Color.green(), timestamp=now
            )
            embed.description = f"{after.mention} was given **{role.name}**"
            await minor_ch.send(embed=embed)
        for role in removed:
            embed = discord.Embed(
                title="Role Removed", color=discord.Color.red(), timestamp=now
            )
            embed.description = f"{after.mention} lost **{role.name}**"
            await minor_ch.send(embed=embed)

        # Timeout (mute) applied / removed
        if before.timed_out_until is None and after.timed_out_until is not None:
            embed = discord.Embed(
                title="Member Timed Out",
                color=discord.Color.dark_orange(),
                timestamp=now,
            )
            embed.description = f"{after.mention} timed out until <t:{int(after.timed_out_until.timestamp())}:F>"
            await ch.send(embed=embed)
        elif before.timed_out_until is not None and after.timed_out_until is None:
            embed = discord.Embed(
                title="Member Timeout Removed",
                color=discord.Color.dark_orange(),
                timestamp=now,
            )
            embed.description = f"{after.mention} is no longer timed out"
            await ch.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        now = time.time()
        self._recent_bans[user.id] = now + IGNORE_WINDOW
        await self._log_simple_event(
            self.modlog_channel_id,
            title="Member Banned",
            description=f"{user.mention} ({user})",
            color=discord.Color.dark_red(),
        )
        for uid, expires in list(self._recent_bans.items()):
            if expires < now:
                self._recent_bans.pop(uid, None)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await self._log_simple_event(
            self.modlog_channel_id,
            title="Member Unbanned",
            description=f"{user.mention} ({user})",
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self._log_simple_event(
            self.modlog_channel_id,
            title="Role Created",
            description=f"**{role.name}** was created",
            color=discord.Color.green(),
            fields=[("Role ID", str(role.id), True)],
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self._log_simple_event(
            self.modlog_channel_id,
            title="Role Deleted",
            description=f"**{role.name}** was deleted",
            color=discord.Color.red(),
            fields=[("Role ID", str(role.id), True)],
        )

    @commands.Cog.listener()
    async def on_guild_role_update(
        self, before: discord.Role, after: discord.Role
    ) -> None:
        changes: list[tuple[str, str, bool]] = []
        if before.name != after.name:
            changes.append(("Name", f"{before.name} → {after.name}", False))
        if before.permissions != after.permissions:
            changes.append(("Permissions", "Changed", False))
        if before.color != after.color:
            changes.append(("Color", f"{before.color} → {after.color}", False))

        if changes:
            await self._log_simple_event(
                self.modlog_channel_id,
                title="Role Updated",
                description=f"Changes to **{before.name}**",
                color=discord.Color.orange(),
                fields=changes,
            )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self._log_simple_event(
            self.modlog_channel_id,
            title="Channel Created",
            description=f"{channel.mention} was created",
            color=discord.Color.green(),
            fields=[("Channel ID", str(channel.id), True)],
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        changes: list[tuple[str, str, bool]] = []
        files: list[discord.File] = []
        if isinstance(before, discord.TextChannel) and before.topic != after.topic:
            raw = f"{before.topic or '[none]'} → {after.topic or '[none]'}"
            val, file = self._maybe_attach_content(raw, prefix="topic")
            changes.append(("Topic", val, False))
            if file:
                files.append(file)
        if before.name != after.name:
            changes.append(("Name", f"{before.name} → {after.name}", False))
        if before.position != after.position:
            changes.append(("Position", f"{before.position} → {after.position}", True))
        if before.category != after.category:
            changes.append(
                (
                    "Category",
                    f"{before.category.name if before.category else 'None'} → "
                    f"{after.category.name if after.category else 'None'}",
                    True,
                )
            )
        if changes:
            await self._log_simple_event(
                self.modlog_channel_id,
                title="Channel Updated",
                description=f"Changes in {after.mention}",
                color=discord.Color.orange(),
                fields=changes,
                files=files or None,
            )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self._log_simple_event(
            self.modlog_channel_id,
            title="Channel Deleted",
            description=f"{channel.name} was deleted",
            color=discord.Color.red(),
            fields=[("Channel ID", str(channel.id), True)],
        )

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
