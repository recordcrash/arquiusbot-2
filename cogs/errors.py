import discord

from discord.ext import commands
from discord import app_commands
from logging import ERROR as LOG_ERROR, CRITICAL as LOG_CRITICAL
from typing import Any, NoReturn

from classes.discordbot import DiscordBot, PlebIgnoredException
from classes.utilities import dummy_awaitable_callable

class Errors(commands.Cog, name="errors"):
    """Errors handler for the base bot."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        bot.tree.error(coro=self.__dispatch_to_app_command_handler)

        self.default_error_message = "D--> There is an error."

    """def help_custom(self):
        emoji = "<a:crossmark:842800737221607474>"
        label = "Error"
        description = "A custom errors handler. Nothing to see here."
        return emoji, label, description"""

    def trace_error(self, level: str, error: Exception) -> NoReturn:
        self.bot.log(
            message=type(error).__name__,
            name=f"discord.{level}",
            level=LOG_ERROR,
            exc_info=error,
        )

        raise error

    async def __dispatch_to_app_command_handler(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        self.bot.dispatch("app_command_error", interaction, error)

    async def __respond_to_interaction(self, interaction: discord.Interaction) -> bool:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    content=self.default_error_message, ephemeral=True
                )
            else:
                await interaction.followup.send(
                    content=self.default_error_message, ephemeral=True
                )
        except discord.errors.NotFound:
            # The original interaction is no longer available; send a new message to the channel.
            await interaction.channel.send(content=self.default_error_message)
        return True

    @commands.Cog.listener("on_error")
    async def get_error(self, event, *args, **kwargs) -> None:
        """Error handler"""
        self.bot.log(
            message=f"Unexpected Internal Error: (event) {event}, (args) {args}, (kwargs) {kwargs}.",
            name="discord.get_error",
            level=LOG_CRITICAL,
        )

    @commands.Cog.listener("on_command_error")
    async def get_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        """Command Error handler
        doc: https://discordpy.readthedocs.io/en/latest/ext/commands/api.html#exception-hierarchy
        """
        edit = dummy_awaitable_callable
        try:
            if ctx.interaction:  # HybridCommand Support
                await self.__respond_to_interaction(ctx.interaction)
                edit = ctx.interaction.edit_original_response
                if isinstance(error, commands.HybridCommandError):
                    error = error.original  # type: ignore # Access to the original error
            else:
                try:
                    discord_message = await ctx.send(self.default_error_message)
                except discord.errors.Forbidden:
                    return
                edit = discord_message.edit
            raise error

        # ConversionError
        except commands.ConversionError as d_error:
            await edit(content=f"D--> {d_error}")
        # UserInputError
        except commands.MissingRequiredArgument as _:
            await edit(
                content=f"D--> Something is missing. `{ctx.clean_prefix}{ctx.command.name} <{'> <'.join(ctx.command.clean_params)}>`"
            )  # type: ignore
        # UserInputError -> BadArgument
        except commands.MemberNotFound or commands.UserNotFound as d_error:
            await edit(
                content=f"D--> Member `{str(d_error).split(' ')[1]}` not found ! Don't hesitate to ping the requested member."
            )
        # UserInputError -> BadUnionArgument | BadLiteralArgument | ArgumentParsingError
        except (
            commands.BadArgument
            or commands.BadUnionArgument
            or commands.BadLiteralArgument
            or commands.ArgumentParsingError
        ) as d_error:
            await edit(content=f"D--> {d_error}")
        # CommandNotFound
        except commands.CommandNotFound as d_error:
            await edit(content=f"D--> Command `{str(d_error).split(' ')[1]}` not found !")
        # CheckFailure
        except commands.PrivateMessageOnly:
            await edit(
                content="D--> This command cannot be used in a guild, try in direct message."
            )
        except commands.NoPrivateMessage:
            await edit(content="D--> This is not working as excpected.")
        except commands.NotOwner:
            await edit(content="D--> You must own this bot to run this command.")
        except commands.MissingPermissions as d_error:
            await edit(
                content=f"D--> Your account require the following permissions: `{'` `'.join(d_error.missing_permissions)}`."
            )
        except commands.BotMissingPermissions as d_error:
            if not "send_messages" in d_error.missing_permissions:
                await edit(
                    content=f"D--> The bot require the following permissions: `{'` `'.join(d_error.missing_permissions)}`."
                )
        except (
            commands.CheckAnyFailure
            or commands.MissingRole
            or commands.BotMissingRole
            or commands.MissingAnyRole
            or commands.BotMissingAnyRole
        ) as d_error:
            await edit(content=f"D--> {d_error}")
        except commands.NSFWChannelRequired:
            await edit(content="D--> This command require an NSFW channel.")
        # DisabledCommand
        except commands.DisabledCommand:
            await edit(content="D--> This command is disabled.")
        # CommandInvokeError
        except commands.CommandInvokeError as d_error:
            await edit(content=f"D--> {d_error.original}")
        # CommandOnCooldown
        except commands.CommandOnCooldown as d_error:
            await edit(
                content=f"D--> Command is on cooldown, wait `{str(d_error).split(' ')[7]}` !"
            )
        # MaxConcurrencyReached
        except commands.MaxConcurrencyReached as d_error:
            await edit(
                content=f"D--> Max concurrency reached. Maximum number of concurrent invokers allowed: `{d_error.number}`, per `{d_error.per}`."
            )
        # HybridCommandError
        except commands.HybridCommandError as d_error:
            await self.get_app_command_error(ctx.interaction, error)  # type: ignore
        except Exception as e:
            self.trace_error("get_command_error", e)

    @commands.Cog.listener("on_app_command_error")
    async def get_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        """App command Error Handler
        doc: https://discordpy.readthedocs.io/en/latest/interactions/api.html#exception-hierarchy
        """
        # If it's our custom error, handle it and return immediately.
        if isinstance(error, PlebIgnoredException):
            if not interaction.response.is_done():
                await interaction.response.send_message(str(error), ephemeral=True)
            else:
                await interaction.followup.send(str(error), ephemeral=True)
            return

        edit = dummy_awaitable_callable
        try:
            await self.__respond_to_interaction(interaction)
            edit = interaction.edit_original_response

            raise error
        except app_commands.CommandInvokeError as d_error:
            if isinstance(d_error.original, discord.errors.InteractionResponded):
                await edit(content=f"D--> {d_error.original}")
            elif isinstance(d_error.original, discord.errors.Forbidden):
                await edit(
                    content=f"D--> `{type(d_error.original).__name__}` : {d_error.original.text}"
                )
            else:
                await edit(
                    content=f"D--> `{type(d_error.original).__name__}` : {d_error.original}"
                )
        except app_commands.CheckFailure as d_error:
            if isinstance(d_error, app_commands.errors.CommandOnCooldown):
                await edit(
                    content=f"D--> Command is on cooldown, wait `{str(d_error).split(' ')[7]}` !"
                )
            else:
                await edit(content=f"D--> `{type(d_error).__name__}` : {d_error}")
        except app_commands.CommandNotFound:
            await edit(
                content=f"D--> Command was not found.. Seems to be a discord bug, probably due to desynchronization.\nMaybe there is multiple commands with the same name, you should try the other one."
            )
        except Exception as e:
            """
            Caught here:
            app_commands.TransformerError
            app_commands.CommandLimitReached
            app_commands.CommandAlreadyRegistered
            app_commands.CommandSignatureMismatch
            """

            self.trace_error("get_app_command_error", e)

    @commands.Cog.listener("on_view_error")
    async def get_view_error(
        self, interaction: discord.Interaction, error: Exception, item: Any
    ) -> None:
        """View Error Handler"""
        try:
            raise error
        except discord.errors.Forbidden:
            pass
        except Exception as e:
            self.trace_error("get_view_error", e)

    @commands.Cog.listener("on_modal_error")
    async def get_modal_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        """Modal Error Handler"""
        try:
            raise error
        except discord.errors.Forbidden:
            pass
        except Exception as e:
            self.trace_error("get_modal_error", e)



async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Errors(bot))