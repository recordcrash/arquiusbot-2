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
SEEN_TTL_DAYS = 14
# Refresh the OAuth token this many seconds before its stated expiry.
TOKEN_REFRESH_MARGIN_SECONDS = 60

# Embed-bar colour per link-flair CSS class, derived from r/homestuck's
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

            # Record first-seen so old entries can be pruned eventually.
            db.record_reddit_post_seen(pid)

            if db.has_reddit_post_been_posted(pid):
                continue
            if score < self.min_score:
                continue

            try:
                await channel.send(
                    embeds=self._build_embeds(post),
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
    def _gallery_image_urls(post: dict[str, Any]) -> list[str]:
        """Returns full-size image URLs for a Reddit gallery post, in order.

        Non-gallery posts return an empty list.
        """
        if not post.get("is_gallery"):
            return []
        items = post.get("gallery_data", {}).get("items") or []
        metadata = post.get("media_metadata") or {}
        urls: list[str] = []
        for item in items:
            mid = item.get("media_id")
            if not mid:
                continue
            meta = metadata.get(mid) or {}
            if meta.get("status") != "valid":
                continue
            source = meta.get("s") or {}
            raw = source.get("u") or source.get("gif") or source.get("mp4")
            if raw:
                urls.append(html.unescape(raw))
        return urls

    @staticmethod
    def _single_preview_image(post: dict[str, Any]) -> str | None:
        """Returns a preview image URL for non-gallery posts, or None."""
        if post.get("post_hint") == "image":
            return post.get("url_overridden_by_dest") or post.get("url")
        previews = post.get("preview", {}).get("images") or []
        if not previews:
            return None
        src = previews[0].get("source", {}).get("url")
        return html.unescape(src) if src else None

    def _build_embeds(self, post: dict[str, Any]) -> list[discord.Embed]:
        """Builds the embed(s) for a post.

        Galleries return up to 4 embeds sharing the same permalink URL;
        Discord stacks same-URL embeds into a single card with multiple
        images. Non-galleries return a single embed with an optional
        preview image.
        """
        gallery_urls = self._gallery_image_urls(post)
        single = self._single_preview_image(post) if not gallery_urls else None
        # Knowing the image URL up-front lets the main-embed builder
        # suppress a redundant description hyperlink when the external
        # URL is the same as the image being shown.
        primary_image = gallery_urls[0] if gallery_urls else single

        main = self._build_main_embed(post, primary_image=primary_image)

        if gallery_urls:
            main.set_image(url=gallery_urls[0])
            extras: list[discord.Embed] = []
            # Discord stacks same-URL embeds — cap at 4 images per card.
            for extra_url in gallery_urls[1:4]:
                extra = discord.Embed(url=main.url)
                extra.set_image(url=extra_url)
                extras.append(extra)
            return [main, *extras]

        if single:
            main.set_image(url=single)
        return [main]

    def _build_main_embed(
        self, post: dict[str, Any], *, primary_image: str | None = None
    ) -> discord.Embed:
        title = (post.get("title") or "(untitled)")[:256]
        permalink = REDDIT_PUBLIC_BASE + (post.get("permalink") or "")
        external = post.get("url_overridden_by_dest") or post.get("url") or permalink
        author = post.get("author") or "[deleted]"
        score = post.get("score", 0)
        comments = post.get("num_comments", 0)
        flair_text = (post.get("link_flair_text") or "").strip()
        flair_class = (post.get("link_flair_css_class") or "").strip().lower()
        subreddit = post.get("subreddit") or self.subreddit
        selftext = post.get("selftext") or ""

        colour_value = FLAIR_COLOURS.get(flair_class, DEFAULT_EMBED_COLOUR)
        embed = discord.Embed(
            title=title,
            url=permalink,
            colour=discord.Colour(colour_value),
        )
        embed.set_author(name=f"u/{author} in r/{subreddit}")

        # Compact stats line as the description's first paragraph. Putting
        # score / comments here instead of using embed.add_field keeps the
        # image directly under the description (Discord's fixed embed layout
        # forces fields above the image).
        stats_bits: list[str] = []
        if flair_text:
            stats_bits.append(f"**[{flair_text}]**")
        stats_bits.append(f"**{score}** points")
        stats_bits.append(f"**{comments}** comments")
        desc_parts: list[str] = [" · ".join(stats_bits)]

        # Show external links inline in the description. Skip:
        #  - self-posts (external is the permalink itself);
        #  - reddit-internal URLs (gallery pages etc.) — already linked via
        #    the embed title;
        #  - the exact URL we're about to render as the embed image,
        #    since repeating it as a hyperlink is redundant (common case:
        #    plain image posts from i.redd.it).
        if (
            external != permalink
            and "reddit.com" not in external
            and external != primary_image
        ):
            desc_parts.append(f"[{external[:60]}]({external})")

        if selftext:
            desc_parts.append(
                selftext[:900] + ("\u2026" if len(selftext) > 900 else "")
            )

        embed.description = "\n\n".join(desc_parts)
        return embed


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(RedditWatcher(bot))
