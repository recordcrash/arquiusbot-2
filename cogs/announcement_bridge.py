"""
Mirrors userbot-forwarded messages from a staging channel into a target channel.

A userbot collects announcements from external channels and forwards them (using
Discord's native forward feature) into the configured source channel. This cog
watches that source channel for forwarded messages, extracts the original content
from the message snapshot, and re-posts it in the target channel with a small
attribution line.

The re-emit:
- prepends a subtext line ``-# From **#<origin-channel-name>**``
- passes content / embeds / attachments from the snapshot through verbatim; and
- suppresses all mentions defensively so an ``@everyone`` in the original
  announcement can't ping the target channel.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import discord
from discord.ext import commands

from classes.discordbot import DiscordBot

MAX_EMBEDS_PER_SEND = 10


class AnnouncementBridge(commands.Cog, name="announcement_bridge"):
    """Re-emits userbot-forwarded posts from a staging channel into a target."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.subconfig_data: dict[str, Any] = self.bot.config.get("cogs", {}).get(
            self.__cog_name__.lower(), {}
        )
        self.source_channel_id: int = int(
            self.subconfig_data.get("source_channel_id", 0)
        )
        self.target_channel_id: int = int(
            self.subconfig_data.get("target_channel_id", 0)
        )
        self.channel_names: dict[int, str] = {
            int(cid): name
            for cid, name in self.subconfig_data.get("channel_names", [])
        }

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message) -> None:
        if not (self.source_channel_id and self.target_channel_id):
            return
        if msg.channel.id != self.source_channel_id:
            return

        # We only care about forwarded messages (native Discord forward feature).
        # Forwarded messages carry one or more MessageSnapshot objects; regular
        # messages have an empty tuple here.
        if not msg.message_snapshots:
            return

        target = self.bot.get_channel(self.target_channel_id)
        if not isinstance(target, discord.abc.Messageable):
            self.bot.log(
                f"AnnouncementBridge: target channel {self.target_channel_id} "
                f"not visible / not messageable",
                name="announcement_bridge",
                level=logging.WARNING,
            )
            return

        snapshot = msg.message_snapshots[0]

        # Try to resolve the origin channel name. Priority:
        # 1. channel_names config map (handles cross-server channels the bot can't see)
        # 2. bot channel cache (works if origin is in the same guild)
        # 3. raw channel ID as fallback
        source_name = "channel"
        if msg.reference is not None and msg.reference.channel_id is not None:
            cid = msg.reference.channel_id
            if cid in self.channel_names:
                source_name = self.channel_names[cid]
            else:
                origin = self.bot.get_channel(cid)
                if origin is not None and hasattr(origin, "name"):
                    source_name = origin.name
                else:
                    source_name = str(cid)

        # Re-fetch attachments from the snapshot so we can re-upload them.
        files: list[discord.File] = []
        for att in snapshot.attachments:
            try:
                data = await att.read()
            except discord.HTTPException as exc:
                self.bot.log(
                    f"AnnouncementBridge: attachment '{att.filename}' "
                    f"unreadable, skipping ({exc})",
                    name="announcement_bridge",
                    level=logging.WARNING,
                )
                continue
            files.append(discord.File(io.BytesIO(data), filename=att.filename))

        content = snapshot.content or ""
        embeds = list(snapshot.embeds or [])

        if not (content or embeds or files):
            return

        prefix = f"-# From **#{source_name}**"
        if content:
            body = f"{prefix}\n{content}"
        else:
            body = prefix

        if len(body) > 2000:
            available = 2000 - len(prefix) - 2
            body = f"{prefix}\n{content[:available]}…"

        try:
            await target.send(
                content=body,
                embeds=embeds[:MAX_EMBEDS_PER_SEND],
                files=files or None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            self.bot.log(
                f"AnnouncementBridge: send failed: {exc}",
                name="announcement_bridge",
                level=logging.ERROR,
            )


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(AnnouncementBridge(bot))
