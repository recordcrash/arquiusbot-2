import discord
import logging

from classes.database import Database
from classes.discordbot import DiscordBot, IgnorePlebsCommandTree
from classes.utilities import (
    load_config,
    clean_close,
    cogs_manager,
    set_logging,
    cogs_directory,
)

from os import listdir


class Bot(DiscordBot):
    def __init__(self, **kwargs) -> None:
        # Default kwargs
        kwargs.setdefault("activity", discord.Game(name="Booting.."))
        kwargs.setdefault("allowed_mentions", discord.AllowedMentions(everyone=False))
        kwargs.setdefault("case_insensitive", True)
        kwargs.setdefault("config", load_config())
        kwargs.setdefault("intents", discord.Intents.all())
        kwargs.setdefault("max_messages", 2500)
        kwargs.setdefault("status", discord.Status.idle)

        super().__init__(**kwargs)

    async def startup(self) -> None:
        """Sync application commands."""
        await self.wait_until_ready()
        synced = await self.tree.sync()
        self.log(
            message=f"Application commands synced ({len(synced)})",
            name="discord.startup",
        )

    async def setup_hook(self) -> None:
        """Initialize the bot, global persistent objects & cogs."""
        await super().setup_hook()

        # Initialize database singleton and attach it to the bot
        self.db = Database()

        # Get all available cogs
        available_cogs = [
            filename[:-3]
            for filename in listdir(cogs_directory)
            if filename.endswith(".py")
        ]

        # Check if specific cogs are enabled in config
        enabled_cogs = self.config["bot"].get("enabled_cogs", [])

        if enabled_cogs:
            # Filter to only include cogs that exist
            valid_cogs = [cog for cog in enabled_cogs if cog in available_cogs]

            if len(valid_cogs) < len(enabled_cogs):
                missing_cogs = set(enabled_cogs) - set(valid_cogs)
                self.log(
                    message=f"Warning: Some enabled cogs were not found: {', '.join(missing_cogs)}",
                    name="discord.setup_hook",
                )

            # Convert to full module paths
            cogs_to_load = [f"cogs.{cog}" for cog in valid_cogs]
            self.log(
                message=f"Loading specified cogs: {', '.join(valid_cogs)}",
                name="discord.setup_hook",
            )
        else:
            # Load all cogs if none specified
            cogs_to_load = [f"cogs.{cog}" for cog in available_cogs]
            self.log(
                message=f"No specific cogs enabled in config, loading all cogs",
                name="discord.setup_hook",
            )

        # Load the selected cogs
        await cogs_manager(self, "load", cogs_to_load)
        self.log(
            message=f"Cogs loaded ({len(cogs_to_load)}): {', '.join(cogs_to_load)}",
            name="discord.setup_hook",
        )

        # Start the startup task.
        self.loop.create_task(self.startup())


if __name__ == "__main__":
    clean_close()
    bot = Bot(
        tree_cls=IgnorePlebsCommandTree,
    )
    bot.logger, streamHandler = set_logging(
        file_level=logging.INFO,
        console_level=logging.INFO,
        filename="discord.log",
    )
    bot.run(
        bot.config["bot"]["token"],
        reconnect=True,
        log_handler=streamHandler,
        log_level=logging.DEBUG,
    )
