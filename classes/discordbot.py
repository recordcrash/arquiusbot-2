from datetime import datetime, timezone
from logging import INFO as LOG_INFO
from logging import Logger, Formatter
from typing import List, TypeVar

from discord import AppInfo, Message, Color, Embed, Guild, app_commands, Interaction, Thread
from discord import __version__ as discord_version
from discord.ext import commands

from cogwatch import watch

from classes.database import Database


class PlebIgnoredException(app_commands.AppCommandError):
    """Raised when a command is disabled in the current channel."""
    def __init__(self, message: str = "D--> Neigh, you may not command me here."):
        super().__init__(message)


class IgnorePlebsCommandTree(app_commands.CommandTree):
    """
    We need to subclass the CommandTree to add an interaction check that blocks
    user commands (not for mods) in certain channels based on the ignoreplebs command.
    """

    async def interaction_check(self, interaction: Interaction) -> bool:
        # First, run the built-in checks.
        if not await super().interaction_check(interaction):
            return False

        # If the user has manage_roles permission, allow.
        if interaction.user.guild_permissions.manage_roles:
            return True

        # Determine the target channel (if in a thread, use its parent).
        target_channel = (
            interaction.channel.parent
            if isinstance(interaction.channel, Thread)
            else interaction.channel
        )

        # Get the list of channels where commands are ignored
        ignore_channels = interaction.client.db.get_channel_category("ignoreplebs")

        # If the channel is in the ignore list, block the interaction.
        if target_channel.id in ignore_channels:
            raise PlebIgnoredException
        return True


def get_prefix(bot, message) -> list:
    """
    Returns the prefix for the bot.

    This is needed so admin commands are only available in certain channels.
    """
    allowed_channels = {
        bot.config["bot"]["usrlog_channel_id"],
        bot.config["bot"]["msglog_channel_id"],
        bot.config["bot"]["modlog_channel_id"],
    }
    # If the message's channel is in the allowed set, return the default prefix;
    # otherwise, return an empty list (meaning no prefix is recognized).
    if message.channel.id in allowed_channels:
        return [bot.config["bot"]["default_prefix"]]
    else:
        return []


class UTCFormatter(Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


Self = TypeVar("Self", bound="DiscordBot")

class DiscordBot(commands.Bot):
    """A Subclass of `commands.Bot`."""

    appinfo: AppInfo
    """Application info for the bot provided by Discord."""

    config: dict
    """The config loaded directly from 'config/*.json'."""

    db: Database | None = None
    """Persistent database object."""

    logger: Logger
    """Logging Object of the bot."""

    prefixes: dict = dict()
    """List of prefixes per guild."""

    uptime: datetime = datetime.now(timezone.utc)
    """Bot's uptime."""

    def __init__(self,**kwargs) -> None:
        """Initialize the bot.
        
        Parameters
        ----------
        config : dict
            By default: The configuration loaded from 'config/*.json'.
        intents : discord.Intents
            Used to enable/disable certain gateway features used by your bot.
        """
        self.config = kwargs.pop("config", None)
        
        if not self.config or not all(item in self.config.keys() for item in ["bot"]): # cogs.json is an optional configuration file
            raise ValueError("Missing required configuration.")

        # Prefix is admin-only, and needs to not be a slash command because it includes slash-command related actions
        kwargs.pop("command_prefix", None) # remove kwarg if exists

        super().__init__(command_prefix = get_prefix, **kwargs)

    def get_current_guild(self) -> Guild:
        """
        Retrieves the default guild (server) using the ID stored in the configuration.
        Raises a ValueError if the guild is not found.
        """
        default_guild_id = self.config["bot"].get("default_guild_id")
        guild = self.get_guild(default_guild_id)
        if guild is None:
            raise ValueError(
                f"Default guild with ID {default_guild_id} not found; check your configuration."
            )
        return guild

    def log(self, message: str, name: str, level: int = LOG_INFO, **kwargs) -> None:
        """Log a message to the console and the log file.

        Parameters
        ----------
        message : str
            The message to log.
        name : str
            The name of the logger.
        level : int
            The level of the log message.
        """
        self.logger.name = name
        self.logger.log(level = level, msg = message, **kwargs)

    @staticmethod
    def create_embed(
        color: Color = Color.red(),
        title: str = None,
        description: str = "",
        footer: str = "",
        timestamp: datetime = None,
    ) -> Embed:
        """
        Returns a standardized embed with a timestamp.
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        embed = Embed(color=color, timestamp=timestamp)
        if description:
            embed.description = description
        if title:
            embed.title = title
        if footer:
            embed.set_footer(text=footer)
        return embed
        
    def __prefix_callable(self: Self, client: Self, message: Message) -> List[str]:
        if message.guild is None:
            return commands.when_mentioned_or(self.config["bot"]["default_prefix"])(client, message)

        if (guild_id := message.guild.id) in client.prefixes: 
            prefix = client.prefixes[guild_id]
        else: 
            prefix = self.config["bot"]["default_prefix"]
        return commands.when_mentioned_or(prefix)(client, message)

    @watch(path='../cogs', preload=True, debug=False)
    async def on_ready(self) -> None:
        self.log( message = f"Logged as: {self.user} | discord.py{discord_version} Guilds: {len(self.guilds)} Users: {len(self.users)} Config: {len(self.config)}", name = "discord.on_ready")

    async def setup_hook(self) -> None:
        # Retrieve the bot's application info
        self.appinfo = await self.application_info()

    async def close(self) -> None:
        await super().close()