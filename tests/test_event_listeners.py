"""Message-delete logging, including forwarded messages."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cogs.event_listeners as E  # noqa: E402

LOG_CHANNEL = 500


@pytest.fixture
def listeners():
    bot = MagicMock()
    bot.config = {"cogs": {"events": {}}, "bot": {"msglog_channel_id": LOG_CHANNEL}}
    cog = E.EventListeners(bot)
    log = MagicMock(spec=discord.TextChannel)
    log.send = AsyncMock()
    bot.get_channel.side_effect = lambda cid: log if cid == LOG_CHANNEL else None
    return cog, log


def attachment(name="nasty.png"):
    return SimpleNamespace(filename=name, proxy_url=f"https://media.example/{name}",
                           url=f"https://cdn.example/{name}")


def message(content="", attachments=(), snapshots=(), reference=None):
    return SimpleNamespace(
        id=9, content=content, attachments=list(attachments),
        message_snapshots=list(snapshots), reference=reference,
        author=SimpleNamespace(id=42, bot=False, mention="<@42>"),
        channel=SimpleNamespace(id=7, mention="<#7>", parent=None),
    )


def sent(log) -> list[discord.Embed]:
    return [call.kwargs["embed"] for call in log.send.await_args_list]


async def test_a_plain_deleted_message_is_logged(listeners):
    cog, log = listeners
    await cog.on_message_delete(message(content="hello"))
    [embed] = sent(log)
    assert embed.title == "Message Deleted" and embed.fields[0].value == "hello"


async def test_a_deleted_forward_logs_what_was_forwarded(listeners):
    cog, log = listeners
    snapshot = SimpleNamespace(content="something nasty", attachments=[attachment()])
    reference = SimpleNamespace(jump_url="https://discord.com/channels/1/2/3")
    await cog.on_message_delete(message(snapshots=[snapshot], reference=reference))

    text, image = sent(log)
    assert text.title == "Forwarded Message Deleted"
    assert text.fields[0].value == "something nasty"
    assert "Forwarded from [this message](https://discord.com/channels/1/2/3)" in text.description
    assert image.image.url == "https://media.example/nasty.png"
    assert "Forwarded" in image.description


async def test_a_forward_without_text_still_logs_its_attachments(listeners):
    cog, log = listeners
    snapshot = SimpleNamespace(content="", attachments=[attachment("doc.pdf")])
    await cog.on_message_delete(message(snapshots=[snapshot]))
    [embed] = sent(log)
    assert embed.title == "File" and embed.fields[0].value == "doc.pdf"
    assert embed.description.endswith(" • Forwarded")
