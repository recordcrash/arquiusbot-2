"""The Reddit card: one Components V2 layout per post.

The tests are async because discord.py 2.6 (what production runs) can only
build a view inside a running event loop; 2.7 lifted that."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cogs.reddit_watcher as R  # noqa: E402


@pytest.fixture
def cog() -> R.RedditWatcher:
    bot = MagicMock()
    bot.config = {"cogs": {"reddit_watcher": {"channel_id": 1}}}
    return R.RedditWatcher(bot)


def post(**overrides) -> dict:
    data = {
        "id": "abc", "title": "A post", "permalink": "/r/homestuck/comments/abc/a_post/",
        "author": "some_user", "score": 42, "num_comments": 7, "subreddit": "homestuck",
        "link_flair_text": "Fanwork", "link_flair_css_class": "fanwork",
        "url": "https://www.reddit.com/r/homestuck/comments/abc/a_post/",
    }
    data.update(overrides)
    return data


def gallery_post(count: int) -> dict:
    ids = [f"m{i}" for i in range(count)]
    return post(
        is_gallery=True,
        url="https://www.reddit.com/gallery/abc",
        gallery_data={"items": [{"media_id": m, "caption": f"panel {i}" if i == 0 else None}
                                for i, m in enumerate(ids)]},
        media_metadata={m: {"status": "valid",
                            "s": {"u": f"https://preview.redd.it/{m}.png?w=1&amp;s=x"}}
                        for m in ids},
    )


def video_post(**overrides) -> dict:
    data = dict(
        is_video=True, post_hint="hosted:video", url="https://v.redd.it/xyz",
        media={"reddit_video": {"fallback_url": "https://v.redd.it/xyz/DASH_720.mp4"}},
        preview={"images": [{"source": {"url": "https://external-preview.redd.it/thumb.jpg?a=1&amp;b=2"}}]},
    )
    data.update(overrides)
    return post(**data)


def parts(view: discord.ui.LayoutView):
    """(container, text, gallery URLs, gallery descriptions) of a card."""
    view.to_components()  # must serialise: Discord rejects malformed layouts
    [container] = view.children
    text = next(c for c in container.children if isinstance(c, discord.ui.TextDisplay))
    gallery = next((c for c in container.children if isinstance(c, discord.ui.MediaGallery)), None)
    items = gallery.items if gallery else []
    return container, text.content, [i.media.url for i in items], [i.description for i in items]


async def test_a_gallery_shows_up_to_ten_images_in_order(cog):
    container, text, urls, captions = parts(cog._build_view(gallery_post(12)))
    assert urls == [f"https://preview.redd.it/m{i}.png?w=1&s=x" for i in range(10)]
    assert captions[0] == "panel 0" and captions[1] is None
    assert "+2 more images" in text


async def test_a_small_gallery_shows_every_image_and_no_remainder(cog):
    container, text, urls, _ = parts(cog._build_view(gallery_post(3)))
    assert len(urls) == 3 and "more images" not in text


async def test_a_video_shows_its_thumbnail_and_is_marked(cog):
    container, text, urls, _ = parts(cog._build_view(video_post()))
    assert urls == ["https://external-preview.redd.it/thumb.jpg?a=1&b=2"]
    assert "▶ video" in text
    assert "v.redd.it" not in text  # no direct video link to half-embed


async def test_an_external_video_links_out_and_shows_its_thumbnail(cog):
    youtube = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    view = cog._build_view(video_post(is_video=False, media={}, post_hint="rich:video",
                                      url=youtube, url_overridden_by_dest=youtube))
    container, text, urls, _ = parts(view)
    assert youtube in text and "▶ video" in text and len(urls) == 1


async def test_an_image_post_shows_the_image_without_repeating_its_link(cog):
    image = "https://i.imgur.com/cat.png"
    container, text, urls, _ = parts(cog._build_view(
        post(post_hint="image", url=image, url_overridden_by_dest=image)))
    assert urls == [image] and image not in text


async def test_a_self_post_is_text_only(cog):
    container, text, urls, _ = parts(cog._build_view(post(selftext="hello " * 400)))
    assert urls == []
    assert text.endswith("…") and "hello" in text


async def test_the_card_carries_the_flair_colour_and_the_header(cog):
    container, text, _, _ = parts(cog._build_view(post()))
    assert container.accent_colour == discord.Colour(R.FLAIR_COLOURS["fanwork"])
    assert text.startswith("-# u/some\\_user in r/homestuck\n### A post")
    assert ("**[Fanwork]** · **42** points · "
            "[**7** comments](https://www.reddit.com/r/homestuck/comments/abc/a_post/)") in text


async def test_the_title_is_a_plain_heading_so_discord_cannot_leak_markdown(cog):
    """Discord fails to parse a masked link whose text contains an emoji
    anywhere, so no link may carry a post title as its text."""
    _, text, _, _ = parts(cog._build_view(post()))
    heading = next(line for line in text.splitlines() if line.startswith("### "))
    assert "](" not in heading


async def test_unknown_flair_gets_reddit_orange(cog):
    container, *_ = parts(cog._build_view(post(link_flair_css_class="mystery")))
    assert container.accent_colour == discord.Colour(R.DEFAULT_EMBED_COLOUR)


async def test_brackets_in_a_title_cannot_form_a_link(cog):
    container, text, _, _ = parts(cog._build_view(post(title="[OC] Karkat [Art]")))
    assert "### \\[OC\\] Karkat \\[Art\\]" in text


async def test_posts_are_sent_as_a_layout_with_no_content_or_embeds(cog):
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    cog.bot.get_channel.return_value = channel
    cog.bot.db.has_reddit_post_been_posted.return_value = False

    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"data": {"children": [{"data": gallery_post(2)}]}})
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=False)
    cog._session = MagicMock(closed=False)
    cog._session.get.return_value = request

    await cog._poll_once()
    kwargs = channel.send.await_args.kwargs
    assert isinstance(kwargs["view"], discord.ui.LayoutView)
    assert "content" not in kwargs and "embeds" not in kwargs and "embed" not in kwargs
    cog.bot.db.mark_reddit_post_posted.assert_called_once_with("abc", 42)


async def test_a_post_older_than_the_cutoff_is_never_posted(cog):
    """A cold start must not flood the channel with the whole listing window."""
    import time as _t
    month_old = post(created_utc=_t.time() - 30 * 86400, score=9999)
    fresh = post(created_utc=_t.time() - 3600, score=9999)
    assert cog._is_too_old(month_old)
    assert not cog._is_too_old(fresh)


async def test_the_age_cutoff_can_be_disabled(cog):
    import time as _t
    cog.max_post_age_days = 0
    assert not cog._is_too_old(post(created_utc=_t.time() - 365 * 86400))


async def test_a_post_with_no_timestamp_is_not_treated_as_old(cog):
    assert not cog._is_too_old(post())


async def test_an_emoji_title_keeps_its_emoji_out_of_any_link(cog):
    """Emoji anywhere in a link's text breaks it; outside the link it is fine."""
    _, text, _, _ = parts(cog._build_view(post(title="megidos 😛")))
    assert "### megidos 😛" in text
    assert "😛]" not in text
