import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from classes.discordbot import DiscordBot
from classes.response_bank import response_bank

logger = logging.getLogger('discord')
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='a')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
handler.setLevel(logging.WARNING)
logger.addHandler(handler)

class LoggingError(Exception):
    """Raised when an error occurs during log reporting."""
    pass

class LogManager(commands.Cog):
    """Handles error logging and reporting to a Discord channel."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.log_channel: discord.TextChannel | None = None

    def cog_unload(self) -> None:
        """Stops the log reporting task when the cog is unloaded."""
        self.report_log.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Starts log reporting once the bot is ready."""
        self.bot.log(message=response_bank.process_logs, name="LogManager.on_ready")
        log_channel_id = self.bot.config["bot"].get("msglog_channel_id")
        if not log_channel_id:
            self.bot.log(
                message=response_bank.process_logs_error.format(
                    error="log channel not found in config"
                ), name="LogManager.on_ready")
            return

        self.log_channel = self.bot.get_channel(log_channel_id)
        if not self.log_channel:
            self.bot.log(
                message=response_bank.process_logs_error.format(
                    error=f"invalid log channel ID: {log_channel_id}"
                ),
                name="LogManager.on_ready",
            )
            return

        self.report_log.start()

    @tasks.loop(hours=24)
    async def report_log(self) -> None:
        """Sends log reports to the configured Discord channel every 24 hours."""
        if not self.log_channel:
            self.bot.log(
                message=response_bank.process_logs_error.format(
                    error="log channel is not set"
                ),
                name="LogManager.report_log",
            )
            return

        now = datetime.now(timezone.utc)

        log_file = 'discord.log'

        # Read log file, send content if not empty
        try:
            with open(log_file, 'rb') as logfile:
                code = logfile.read()
                if code:
                    logfile.seek(0)
                    await self.log_channel.send(
                        f"ArquiusBot Log @ {now}",
                        file=discord.File(logfile, 'errors.log')
                    )
        except FileNotFoundError:
            self.bot.log(
                message=response_bank.process_logs_error.format(
                    error="log file not found"
                ),
                name="LogManager.report_log",
            )
            return

        # Truncate log file
        try:
            with open(log_file, 'w') as logfile:
                logfile.truncate(0)
        except Exception as e:
            self.bot.log(
                message=response_bank.process_logs_error.format(
                    error=f"failed to clear log file: {e}"
                ),
                name="LogManager.report_log",
            )

    @app_commands.guild_only
    @app_commands.command(name="reportlog", description="Manually post the bot's error logs.")
    @app_commands.default_permissions(administrator=True)
    async def force_report_log(self, interaction: discord.Interaction) -> None:
        """Allows manually triggering log reporting."""
        if not self.log_channel:
            await interaction.response.send_message("Log channel not found.", ephemeral=True)
            return

        now = datetime.now(timezone.utc)
        log_file = 'discord.log'

        try:
            with open(log_file, 'rb') as logfile:
                code = logfile.read()
                if not code:
                    await interaction.response.send_message("No logs to report.", ephemeral=True)
                    return

                logfile.seek(0)
                await self.log_channel.send(
                    f"ArquiusBot Log @ {now}",
                    file=discord.File(logfile, 'errors.log')
                )

            # Truncate the log file
            with open(log_file, 'w') as logfile:
                logfile.truncate(0)

            await interaction.response.send_message("Log report sent successfully.", ephemeral=True)
        except FileNotFoundError:
            await interaction.response.send_message("Log file not found.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error while reporting logs: {e}", ephemeral=True)

async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(LogManager(bot))
