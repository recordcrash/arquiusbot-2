import logging
from discord.ext import commands, tasks
import discord
from aiohttp import ClientSession

from classes.discordbot import DiscordBot


class Status(commands.Cog, name="status"):
    """A cog to set the current status of the bot and optionally ping Uptime Kuma."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        # Read config for this cog
        self.subconfig = self.bot.config.get("cogs", {}).get(
            self.__cog_name__.lower(), {}
        )
        # Status rotation
        self.status_list = self.subconfig.get("status", [])
        self.status_interval = self.subconfig.get("status_interval", 300)
        self.count = 0
        # Heartbeat settings
        self.push_url = self.subconfig.get("push_url")
        self.heartbeat_interval = self.subconfig.get("heartbeat_interval", 300)
        self._session = None

    async def cog_load(self) -> None:
        # Start status loop if configured
        if self.status_list:
            self.task_change_status.change_interval(seconds=self.status_interval)
            self.task_change_status.start()
        # Start heartbeat loop if configured
        if self.push_url:
            self._session = ClientSession()
            self.heartbeat_loop.change_interval(seconds=self.heartbeat_interval)
            self.heartbeat_loop.start()
        else:
            self.bot.log(
                "Status cog: no push_url configured; skipping heartbeat",
                name="status",
                level=logging.INFO,
            )

    async def cog_unload(self) -> None:
        if self.task_change_status.is_running():
            self.task_change_status.cancel()
        if self.push_url and self.heartbeat_loop.is_running():
            self.heartbeat_loop.cancel()
        if self._session:
            await self._session.close()

    @tasks.loop()
    async def task_change_status(self) -> None:
        await self.bot.wait_until_ready()
        # cycle through provided status messages
        current = self.status_list[self.count]
        await self.bot.change_presence(
            activity=discord.Game(name=current), status=discord.Status.online
        )
        self.count = (self.count + 1) % len(self.status_list)

    @tasks.loop()
    async def heartbeat_loop(self) -> None:
        try:
            async with self._session.get(self.push_url, timeout=10) as resp:
                if resp.status != 200:
                    self.bot.log(
                        f"Heartbeat returned status {resp.status}",
                        name="status",
                        level=logging.WARNING,
                    )
        except Exception as e:
            self.bot.log(
                f"Heartbeat exception: {e}", name="status", level=logging.ERROR
            )


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Status(bot))
