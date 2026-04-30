"""
Mirrors Discord-Follow cross-posts from a channel into another.

This cog watches the source channel, filters to messages flagged as
``IS_CROSSPOST``, and re-emits each as a regular bot message in the
target channel. The re-emit:

- prepends a small subtext line ``-# From **#<source-channel-name>**``
- passes content / embeds / attachments through verbatim; and
- suppresses all mentions defensively, so an ``@everyone`` in the
  original announcement can't ping modchat.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import discord
from discord.ext import commands

from classes.discordbot import DiscordBot

# Discord caps each send at 10 embeds.
MAX_EMBEDS_PER_SEND = 10


class AnnouncementBridge(commands.Cog, name="announcement_bridge"):
    """Re-emits Follow-feature cross-posts from a hub channel into a target."""

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

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message) -> None:
        if not (self.source_channel_id and self.target_channel_id):
            return
        if msg.channel.id != self.source_channel_id:
            return
        # Filter strictly to Follow cross-posts, ignore anything else
        # someone might post in the hub channel.
        if not msg.flags.is_crossposted:
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

        # Discord names the cross-post webhook user after the source
        # channel (no server suffix). Strip any leading '#' and re-add
        # one for visual consistency in the prefix line.
        source_name = (msg.author.name or "").lstrip("#").strip() or "channel"

        # Re-fetch attachments so we can re-upload them as bot files
        # (the original URLs are signed for the hub-channel scope).
        files: list[discord.File] = []
        for att in msg.attachments:
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

        # Skip truly empty payloads (nothing to relay).
        if not (msg.content or msg.embeds or files):
            return

        # Subtext
        prefix = f"-# From **#{source_name}**"
        if msg.content:
            body = f"{prefix}\n{msg.content}"
        else:
            body = prefix
        # Discord caps content at 2000 chars; if the original was longer
        # the prefix could push us over. Truncate the original tail.
        if len(body) > 2000:
            available = 2000 - len(prefix) - 2  # for "\n" + final char
            body = f"{prefix}\n{(msg.content or '')[:available]}…"

        try:
            await target.send(
                content=body,
                embeds=(msg.embeds or [])[:MAX_EMBEDS_PER_SEND],
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
