import csv
import io
import os
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

# Optional CSV with historical user activity from the old Homestuck Discord.
# Loaded once at cog init; gracefully absent if the file doesn't exist.
# Schema: id,name,message_count,last_post_date
HSD_USER_LIST_FILE = os.path.join("data", "hsd_user_list.csv")


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

        # Threshold for total reactions on a bot message to trigger a ping
        self.reaction_ping_threshold: int = 9

        # Tracks messages already reported so they are only pinged once
        self._reaction_pinged_messages: set[int] = set()

        # Estimated total reactions per message id to reduce fetches
        self._reaction_estimate: dict[int, int] = defaultdict(int)

        # Legacy-HSD activity map: user_id -> {name, message_count, last_post_date}.
        # Empty dict if the optional CSV file isn't present.
        self.hsd_user_data: dict[int, dict[str, str]] = self._load_hsd_user_list()
        if self.hsd_user_data:
            self.bot.log(
                message=f"Loaded {len(self.hsd_user_data)} HSD user records "
                f"from {HSD_USER_LIST_FILE}",
                name="EventListeners",
            )

    @staticmethod
    def _load_hsd_user_list() -> dict[int, dict[str, str]]:
        """Reads ``data/hsd_user_list.csv`` into a dict keyed by user id.

        Returns an empty dict if the file is missing or unreadable — the
        feature is optional and the rest of the cog works fine without
        it.
        """
        if not os.path.exists(HSD_USER_LIST_FILE):
            return {}
        out: dict[int, dict[str, str]] = {}
        try:
            with open(HSD_USER_LIST_FILE, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    raw_id = (row.get("id") or "").strip()
                    try:
                        uid = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    out[uid] = {
                        "name": (row.get("name") or "").strip(),
                        "message_count": (row.get("message_count") or "").strip(),
                        "last_post_date": (row.get("last_post_date") or "").strip(),
                    }
        except OSError:
            return {}
        return out

    def _hsd_embed_fields(self, user_id: int) -> list[tuple[str, str, bool]]:
        """Returns the extra embed fields for a known legacy-HSD user, or []."""
        entry = self.hsd_user_data.get(user_id)
        if not entry:
            return []

        # Format message count with thousands separators (e.g. "1,836,244").
        raw_count = entry.get("message_count", "")
        try:
            count_display = f"{int(raw_count):,}"
        except (TypeError, ValueError):
            count_display = raw_count or "unknown"

        # Parse the last-post date (CSV format "YYYY-MM-DD HH:MM:SS", UTC).
        # Render as a Discord relative timestamp so it reads naturally
        # ("3 weeks ago"); fall back to the raw string if parsing fails.
        raw_date = entry.get("last_post_date", "")
        try:
            dt = datetime.fromisoformat(raw_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            date_display = self._relative_ts(dt)
        except (TypeError, ValueError):
            date_display = raw_date or "unknown"

        nickname = entry.get("name") or "unknown"

        return [
            ("Last Seen in HSD", date_display, True),
            ("Number of Messages", count_display, True),
            ("Nickname", nickname, True),
        ]

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
                # If we have legacy-HSD activity data for this user, surface it.
                for field_name, field_value, inline in self._hsd_embed_fields(
                    member.id
                ):
                    embed.add_field(name=field_name, value=field_value, inline=inline)
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
            embed = discord.Embed(color=discord.Color.green(), timestamp=now)
            embed.description = f"{after.mention} was given **{role.name}**"
            # If role name contains the substring "-ban", send to modlog instead
            if "-ban" in role.name.lower():
                embed.title = "Role Added (Ban)"
                await ch.send(embed=embed)
            else:
                embed.title = "Role Added"
                await minor_ch.send(embed=embed)
        for role in removed:
            embed = discord.Embed(
                title="Role Removed", color=discord.Color.red(), timestamp=now
            )
            embed.description = f"{after.mention} lost **{role.name}**"
            if "-ban" in role.name.lower():
                embed.title = "Role Removed (Ban)"
                await ch.send(embed=embed)
            else:
                embed.title = "Role Removed"
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
        # Bookkeeping only: the ban embed itself is emitted from
        # on_audit_log_entry_create, which receives reason + moderator
        # directly from Discord's gateway payload. We still need the
        # _recent_bans window here because this event fires first and
        # is what suppresses message-delete logs during the purge.
        now = time.time()
        self._recent_bans[user.id] = now + IGNORE_WINDOW
        for uid, expires in list(self._recent_bans.items()):
            if expires < now:
                self._recent_bans.pop(uid, None)

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry) -> None:
        """Handles Discord audit-log push events.

        Currently scoped to ban actions: we emit the "Member Banned"
        modlog embed from here so we can read ``entry.reason`` and
        ``entry.user`` (the moderator) directly from the gateway
        payload — no extra HTTP fetch, no race against on_member_ban.
        """
        if entry.action != discord.AuditLogAction.ban:
            return

        target = entry.target
        if target is None:
            return

        # entry.target is typically a User for ban actions, but can be
        # a discord.Object when the user is uncached. Render both.
        if isinstance(target, (discord.User, discord.Member)):
            description = f"{target.mention} ({target})"
        else:
            description = f"<@{target.id}>"

        fields: list[tuple[str, str, bool]] = []
        if entry.reason:
            # Discord embed field values cap at 1024 chars.
            fields.append(("Reason", entry.reason[:1024], False))
        if entry.user is not None:
            mod = entry.user
            fields.append(("Banned by", f"{mod.mention} ({mod})", True))

        await self._log_simple_event(
            self.modlog_channel_id,
            title="Member Banned",
            description=description,
            color=discord.Color.dark_red(),
            fields=fields or None,
        )

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
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Pings a user when a bot message crosses the reaction threshold."""
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        mid = payload.message_id

        # Avoid duplicate notifications using memory, falling back to database
        if mid in self._reaction_pinged_messages:
            return
        if self.bot.db.has_reaction_ping(mid):
            self._reaction_pinged_messages.add(mid)
            return

        # Seed from the real total once per message to avoid stale starts after restarts
        if self._reaction_estimate.get(mid, 0) == 0:
            try:
                msg = await channel.fetch_message(mid)
            except (discord.NotFound, discord.HTTPException):
                return
            if not msg.author or msg.author.id != self.bot.user.id:
                return
            real_total = sum(r.count for r in msg.reactions)
            self._reaction_estimate[mid] = real_total
            if real_total <= self.reaction_ping_threshold:
                return
            message = msg
            total_reactions = real_total
        else:
            # Increment local estimate and only refetch when we think we crossed the threshold
            self._reaction_estimate[mid] += 1
            if self._reaction_estimate[mid] <= self.reaction_ping_threshold:
                return
            try:
                message = await channel.fetch_message(mid)
            except (discord.NotFound, discord.HTTPException):
                return
            if not message.author or message.author.id != self.bot.user.id:
                return
            total_reactions = sum(r.count for r in message.reactions)
            self._reaction_estimate[mid] = total_reactions
            if total_reactions <= self.reaction_ping_threshold:
                return

        # Determine where to notify
        target_channel: discord.abc.MessageableChannel | None = None
        if self.modlog_channel_id:
            target_channel = self.bot.get_channel(self.modlog_channel_id)
        if target_channel is None:
            target_channel = message.channel

        # Build the mention and embed
        ping_uid = self.bot.config["cogs"]["daily_counter"]["ping_user_id"]
        if not ping_uid:
            return
        admin_mention = f"<@{ping_uid}>"

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="Reaction Threshold Reached",
            description=f"Bot message in {message.channel.mention} exceeded {self.reaction_ping_threshold} reactions  • [Jump to Message]({message.jump_url})",
            color=discord.Color.gold(),
            timestamp=now,
        )
        embed.add_field(name="Total Reactions", value=str(total_reactions), inline=True)
        embed.add_field(name="Message ID", value=str(message.id), inline=True)

        try:
            await target_channel.send(admin_mention, embed=embed)
        except discord.Forbidden:
            self.bot.log(
                message=f"Forbidden to send threshold ping to {getattr(target_channel, 'id', 'unknown')}",
                name="on_raw_reaction_add",
            )
        except discord.HTTPException as e:
            self.bot.log(
                message=f"HTTP error sending threshold ping: {e}",
                name="on_raw_reaction_add",
            )
        else:
            # Record success in memory and database
            self._reaction_pinged_messages.add(message.id)
            gid = message.guild.id if message.guild else None
            self.bot.db.add_reaction_ping(
                message_id=message.id,
                channel_id=message.channel.id,
                guild_id=gid,
                ping_time=now,
                total_reactions=total_reactions,
                threshold=self.reaction_ping_threshold,
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
