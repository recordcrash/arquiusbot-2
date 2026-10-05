from __future__ import annotations

import html
import logging
import time
from typing import Any

import aiohttp
import discord
from discord.ext import commands, tasks

from classes.discordbot import DiscordBot

REDDIT_PUBLIC_BASE = "https://www.reddit.com"
REDDIT_OAUTH_BASE = "https://oauth.reddit.com"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
DEFAULT_USER_AGENT = "arquiusbot/1.0 reddit-watcher"
# Dedup rows are pruned after this long. It must stay comfortably longer
# than the span of posts a /new?limit=N listing covers, or a post can be
# pruned while still in the listing and get posted a second time.
SEEN_TTL_DAYS = 180
# Refresh the OAuth token this many seconds before its stated expiry.
TOKEN_REFRESH_MARGIN_SECONDS = 60
# Host substrings that identify a URL as pointing to Reddit-owned media /
# pages. Used to skip the external-link line for URLs that are already
# represented by the title link or the gallery.
REDDIT_HOST_SUBSTRINGS = ("reddit.com", "redd.it")
# Discord's limit on items in one media gallery.
MAX_GALLERY_ITEMS = 10

# Card accent colour per link-flair CSS class, derived from r/homestuck's
# subreddit CSS. For flairs whose background is light grey, we use the text
# colour instead (more distinctive). 0x000000 is treated as "no colour" by
# Discord, so pure-black flairs use 0x010101 as a workaround.
DEFAULT_EMBED_COLOUR = 0xFF8700  # Reddit orange, for unknown / missing flairs.
FLAIR_COLOURS: dict[str, int] = {
    "fanwork": 0xB536DA,
    "cosplay": 0xE00707,
    "meta": 0x03460E,
    "discussion": 0x0715CD,
    "hiveswap": 0xE00707,
    "theory": 0x4AC925,
    "humor": 0x4AC925,
    "news": 0x00D5F2,
    "cs": 0xF2A400,
    "update": 0xFF8C00,  # label: "OFFICIAL"
    "sighting": 0x00D5F2,
    "fanventure": 0x1F9400,
    "show": 0xFF044B,
    "psycholonials": 0x010101,
    "modannounce": 0xFF6FF2,  # label: "ANNOUNCEMENT"
    "shitpost": 0x3D1F00,
}


class RedditWatcher(commands.Cog, name="reddit_watcher"):
    """
    Polls a subreddit and reposts submissions that cross a score threshold
    into a configured Discord channel. Each submission is only posted once;
    state is persisted in the bot's SQLite database.

    Uses Reddit's OAuth2 API (``oauth.reddit.com``) when credentials are
    provided; falls back to the anonymous ``.json`` endpoint otherwise.
    OAuth is required from most cloud/datacenter hosts (DigitalOcean,
    AWS, etc.) since Reddit blocks anonymous requests from those IP
    ranges. Credentials come from a "script" app registered at
    https://www.reddit.com/prefs/apps . We use the ``client_credentials``
    grant, which authenticates the app as itself (no user account) and
    is sufficient for reading public subreddit data.

    Config keys (``config/cogs*.json`` under ``reddit_watcher``):
        subreddit         str   default "homestuck"
        channel_id        int   required — cog is a no-op if 0/missing
        min_score         int   default 30
        interval_minutes  int   default 10
        fetch_limit       int   default 25  (capped at 100)
        user_agent        str   default "arquiusbot/1.0 reddit-watcher"
        client_id         str   OAuth client id   (optional)
        client_secret     str   OAuth client secret   (optional)
    """

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.subconfig_data: dict[str, Any] = self.bot.config.get("cogs", {}).get(
            self.__cog_name__.lower(), {}
        )

        self.subreddit: str = self.subconfig_data.get("subreddit", "homestuck")
        self.channel_id: int = int(self.subconfig_data.get("channel_id", 0))
        self.min_score: int = int(self.subconfig_data.get("min_score", 30))
        self.max_post_age_days: int = int(
            self.subconfig_data.get("max_post_age_days", 7)
        )
        self.interval_minutes: int = int(
            self.subconfig_data.get("interval_minutes", 10)
        )
        self.fetch_limit: int = min(
            100, int(self.subconfig_data.get("fetch_limit", 25))
        )
        self.user_agent: str = self.subconfig_data.get("user_agent", DEFAULT_USER_AGENT)

        # OAuth credentials — when both are non-empty, the cog uses the
        # authenticated API path via the client_credentials grant.
        self.client_id: str = self.subconfig_data.get("client_id", "") or ""
        self.client_secret: str = self.subconfig_data.get("client_secret", "") or ""

        self._session: aiohttp.ClientSession | None = None
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def has_oauth_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def cog_load(self) -> None:
        if not self.channel_id:
            self.bot.log(
                "RedditWatcher: no channel_id configured; cog disabled.",
                name="reddit_watcher",
                level=logging.WARNING,
            )
            return
        self._session = aiohttp.ClientSession(headers={"User-Agent": self.user_agent})
        self.poll.change_interval(minutes=self.interval_minutes)
        self.poll.start()

    async def cog_unload(self) -> None:
        if self.poll.is_running():
            self.poll.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    @tasks.loop()
    async def poll(self) -> None:
        try:
            await self._poll_once()
        except Exception as exc:
            self.bot.log(
                f"RedditWatcher poll errored: {exc}",
                name="reddit_watcher",
                level=logging.ERROR,
            )

    @poll.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def _fetch_access_token(self) -> str | None:
        """Request a new Reddit OAuth access token via client_credentials.

        Returns the token string on success, ``None`` on failure. Tokens
        issued by Reddit last ~24h by default; we refresh 60s before
        expiry to avoid edge-of-clock races. No user account is
        involved — this is app-only OAuth, sufficient for reading
        public subreddit data.
        """
        if self._session is None or self._session.closed:
            return None
        auth = aiohttp.BasicAuth(self.client_id, self.client_secret)
        data = {"grant_type": "client_credentials"}
        try:
            async with self._session.post(
                REDDIT_TOKEN_URL,
                auth=auth,
                data=data,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    self.bot.log(
                        f"RedditWatcher: token fetch HTTP {resp.status}: {body}",
                        name="reddit_watcher",
                        level=logging.ERROR,
                    )
                    return None
                payload = await resp.json()
        except aiohttp.ClientError as exc:
            self.bot.log(
                f"RedditWatcher: token fetch failed: {exc}",
                name="reddit_watcher",
                level=logging.ERROR,
            )
            return None

        token = payload.get("access_token")
        if not token:
            self.bot.log(
                f"RedditWatcher: token response missing access_token: {payload}",
                name="reddit_watcher",
                level=logging.ERROR,
            )
            return None
        expires_in = int(payload.get("expires_in") or 3600)
        self._access_token = token
        self._token_expires_at = time.time() + expires_in - TOKEN_REFRESH_MARGIN_SECONDS
        return token

    async def _get_valid_token(self) -> str | None:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        return await self._fetch_access_token()

    async def _poll_once(self) -> None:
        if self._session is None or self._session.closed:
            return
        db = self.bot.db
        if db is None:
            return

        # Build request: OAuth if credentials present, anonymous fallback
        # otherwise. Anonymous will fail with 403 from cloud/datacenter
        # IPs (DigitalOcean, AWS, etc.) — the log message will make the
        # cause explicit if it happens.
        if self.has_oauth_credentials:
            token = await self._get_valid_token()
            if token is None:
                return  # _fetch_access_token already logged the reason
            url = (
                f"{REDDIT_OAUTH_BASE}/r/{self.subreddit}"
                f"/new?limit={self.fetch_limit}"
            )
            req_headers = {"Authorization": f"Bearer {token}"}
        else:
            url = (
                f"{REDDIT_PUBLIC_BASE}/r/{self.subreddit}"
                f"/new.json?limit={self.fetch_limit}"
            )
            req_headers = {}

        try:
            async with self._session.get(
                url,
                headers=req_headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 401 and self.has_oauth_credentials:
                    # Force a refresh on the next poll in case the
                    # server-side invalidated our token early.
                    self._access_token = None
                    self.bot.log(
                        "RedditWatcher: OAuth token rejected (401); "
                        "will refresh on next poll",
                        name="reddit_watcher",
                        level=logging.WARNING,
                    )
                    return
                if resp.status != 200:
                    self.bot.log(
                        f"RedditWatcher: {url} -> HTTP {resp.status}",
                        name="reddit_watcher",
                        level=logging.WARNING,
                    )
                    return
                payload: dict[str, Any] = await resp.json()
        except aiohttp.ClientError as exc:
            self.bot.log(
                f"RedditWatcher: request failed: {exc}",
                name="reddit_watcher",
                level=logging.WARNING,
            )
            return

        posts = [child["data"] for child in payload.get("data", {}).get("children", [])]
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            self.bot.log(
                f"RedditWatcher: channel id {self.channel_id} not visible to bot",
                name="reddit_watcher",
                level=logging.WARNING,
            )
            return
        if not isinstance(channel, discord.abc.Messageable):
            self.bot.log(
                f"RedditWatcher: channel id {self.channel_id} is not messageable",
                name="reddit_watcher",
                level=logging.WARNING,
            )
            return

        for post in posts:
            pid = post.get("id")
            if not pid:
                continue
            score = int(post.get("score") or 0)

            if self._is_too_old(post):
                continue

            # Record first-seen so old entries can be pruned eventually.
            db.record_reddit_post_seen(pid)

            if db.has_reddit_post_been_posted(pid):
                continue
            if score < self.min_score:
                continue
            # Skip NSFW submissions (Reddit's ``over_18`` flag) — even if
            # they cross the score threshold we don't want them surfaced
            # in the channel.
            if post.get("over_18"):
                continue

            try:
                await channel.send(
                    view=self._build_view(post),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException as exc:
                self.bot.log(
                    f"RedditWatcher: failed to post {pid}: {exc}",
                    name="reddit_watcher",
                    level=logging.ERROR,
                )
                continue

            db.mark_reddit_post_posted(pid, score)
            self.bot.log(
                f"RedditWatcher: posted r/{self.subreddit} {pid} "
                f"(score={score}): {(post.get('title') or '')[:120]}",
                name="reddit_watcher",
                level=logging.INFO,
            )

        db.prune_reddit_posts(SEEN_TTL_DAYS)

    @staticmethod
    def _gallery_images(post: dict[str, Any]) -> list[tuple[str, str | None]]:
        """(full-size URL, caption) for each image of a gallery post, in
        order. Non-gallery posts return an empty list."""
        if not post.get("is_gallery"):
            return []
        items = (post.get("gallery_data") or {}).get("items") or []
        metadata = post.get("media_metadata") or {}
        images: list[tuple[str, str | None]] = []
        for item in items:
            meta = metadata.get(item.get("media_id")) or {}
            if meta.get("status") != "valid":
                continue
            source = meta.get("s") or {}
            raw = source.get("u") or source.get("gif") or source.get("mp4")
            if raw:
                images.append((html.unescape(raw), item.get("caption") or None))
        return images

    @staticmethod
    def _single_preview_image(post: dict[str, Any]) -> str | None:
        """The post's image, or for anything else (videos and links included)
        Reddit's preview thumbnail; None if there is neither."""
        if post.get("post_hint") == "image":
            return post.get("url_overridden_by_dest") or post.get("url")
        previews = (post.get("preview") or {}).get("images") or []
        if not previews:
            return None
        src = (previews[0].get("source") or {}).get("url")
        return html.unescape(src) if src else None

    def _is_too_old(self, post: dict[str, Any]) -> bool:
        """True if the submission predates the age cutoff."""
        if not self.max_post_age_days:
            return False
        created = float(post.get("created_utc") or 0)
        if not created:
            return False
        return (time.time() - created) > self.max_post_age_days * 86400

    @staticmethod
    def _is_video(post: dict[str, Any]) -> bool:
        media = post.get("media") or {}
        return bool(post.get("is_video") or media.get("reddit_video")
                    or post.get("post_hint") in ("hosted:video", "rich:video"))

    def _build_view(self, post: dict[str, Any]) -> discord.ui.LayoutView:
        """
        The post as one Components V2 card: a container in the flair's
        colour holding the text and, below it, a gallery of the post's
        images (up to Discord's 10). Videos show their thumbnail only and
        are marked as such; clicking through to Reddit plays them. Discord
        can't play Reddit's split audio/video streams inline anyway.
        """
        images = self._gallery_images(post)
        if not images:
            single = self._single_preview_image(post)
            images = [(single, None)] if single else []
        shown = images[:MAX_GALLERY_ITEMS]

        container = discord.ui.Container(
            accent_colour=discord.Colour(self._colour(post)))
        text = self._build_text(
            post,
            displayed_url=shown[0][0] if shown else None,
            more_images=len(images) - len(shown),
        )
        container.add_item(discord.ui.TextDisplay(text))
        if shown:
            gallery = discord.ui.MediaGallery()
            for url, caption in shown:
                gallery.add_item(media=url, description=caption[:1024] if caption else None)
            container.add_item(gallery)

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        return view

    @staticmethod
    def _colour(post: dict[str, Any]) -> int:
        flair_class = (post.get("link_flair_css_class") or "").strip().lower()
        return FLAIR_COLOURS.get(flair_class, DEFAULT_EMBED_COLOUR)

    def _build_text(
        self,
        post: dict[str, Any],
        *,
        displayed_url: str | None = None,
        more_images: int = 0,
    ) -> str:
        title = escape_link_text(post.get("title") or "(untitled)")[:300]
        permalink = REDDIT_PUBLIC_BASE + (post.get("permalink") or "")
        external = post.get("url_overridden_by_dest") or post.get("url") or permalink
        author = discord.utils.escape_markdown(post.get("author") or "[deleted]")
        score = post.get("score", 0)
        comments = post.get("num_comments", 0)
        flair_text = (post.get("link_flair_text") or "").strip()
        subreddit = post.get("subreddit") or self.subreddit
        selftext = post.get("selftext") or ""

        stats_bits: list[str] = []
        if flair_text:
            stats_bits.append(f"**[{discord.utils.escape_markdown(flair_text)}]**")
        stats_bits.append(f"**{score}** points")
        stats_bits.append(f"[**{comments}** comments]({permalink})")
        if self._is_video(post):
            stats_bits.append("▶ video")
        if more_images > 0:
            stats_bits.append(f"+{more_images} more images")

        parts: list[str] = [
            f"-# u/{author} in r/{subreddit}\n### {title}",
            " · ".join(stats_bits),
        ]

        # Show external links. Skip:
        #  - self-posts (external is the permalink itself);
        #  - any Reddit-owned URL (reddit.com gallery page, v.redd.it
        #    video, i.redd.it image, etc.) — already represented by the
        #    title link or the gallery;
        #  - the exact URL already being shown as the first image.
        is_reddit_owned = any(h in external for h in REDDIT_HOST_SUBSTRINGS)
        if external != permalink and not is_reddit_owned and external != displayed_url:
            parts.append(f"[{escape_link_text(external[:60])}]({external})")

        if selftext:
            parts.append(selftext[:900] + ("\u2026" if len(selftext) > 900 else ""))

        return "\n\n".join(parts)


def escape_link_text(text: str) -> str:
    """Markdown-safe text for inside [...]: brackets would end the link early."""
    text = discord.utils.escape_markdown(text)
    return text.replace("[", "\\[").replace("]", "\\]")


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(RedditWatcher(bot))
