import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from classes.utilities import user_has_role
from classes.ai import AIClient
from classes.discordbot import DiscordBot
from classes.response_bank import url_bank  # Constant for Drewbot's avatar URL


class DrewBotCog(commands.Cog, name="drewbot"):
    """
    Lets Patrons interact with Drewbot.
    """

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        # Bot data
        self.censored_words = self.bot.config["bot"]["censored_words"]
        self.censor_character = self.bot.config["bot"]["censor_character"]
        # Cog exclusive data
        self.subconfig_data = self.bot.config["cogs"][self.__cog_name__.lower()]
        self.api_key = self.subconfig_data["openai_api_key"]
        self.patron_role_ids = self.subconfig_data["patron_role_ids"]
        self.model_choices = self.subconfig_data["drewbot_model_choices"]
        self.choices: list[app_commands.Choice[str]] = []
        for choice in self.model_choices:
            display_name = f"{choice['name']} - {choice['description']}"
            self.choices.append(
                app_commands.Choice(name=display_name, value=choice["id"])
            )
        self.base_temperature = self.subconfig_data["base_temperature"]
        self.system_prompt = self.subconfig_data["system_prompt"]
        self.username = self.subconfig_data["username"]
        self.username_full = self.subconfig_data["username_full"]
        # Storage variables
        self.server_emotes: dict[str, str] = {}
        # {(channel_id, discord_msg_id): (openai_response_id, datetime, model_id, temperature, label)}
        self.active_conversations: dict[
            (int, int), (str, datetime, str, float, str)
        ] = {}
        self.edit_lock = asyncio.Lock()
        self.last_edit: datetime | None = None  # timestamp of the most recent edit
        self.conversation_timeout = timedelta(minutes=30)
        self.prune_conversations.start()

    async def initialize_emotes(self) -> dict[str, str]:
        guild = self.bot.get_current_guild()
        return {emote.name: f"<:{emote.name}:{emote.id}>" for emote in guild.emojis}

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.server_emotes = await self.initialize_emotes()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        If someone replies to a Drewbot message we still remember,
        continue the conversation from that point.
        """
        if (
            message.author.bot
            or message.guild is None
            or message.reference is None
            or message.reference.message_id is None
        ):
            return

        author_is_patron = any(
            role.id in self.patron_role_ids
            for role in getattr(message.author, "roles", [])
        )
        if not author_is_patron:
            return

        key = (message.channel.id, message.reference.message_id)
        convo = self.active_conversations.get(key)
        if convo is None:
            return  # not an active Drewbot thread

        prev_resp_id, _ts, model_id, temp, label = convo
        await self._drewbot_respond(
            interaction=None,
            channel=message.channel,
            author=message.author,
            prompt=message.clean_content,
            model_id=model_id,
            label=label,
            temperature=temp,
            prev_resp_id=prev_resp_id,
            reply_target=message,
        )

    async def _drewbot_respond(
        self,
        *,
        interaction: discord.Interaction | None,
        channel: discord.abc.Messageable,
        author: discord.Member | discord.User,
        prompt: str,
        model_id: str,
        label: str,
        temperature: float,
        prev_resp_id: str | None = None,
        reply_target: discord.Message | None = None,
        show_user_embed: bool = False,
    ) -> None:
        """Runs the OpenAI stream and handles embeds/edits."""
        openai_client = AIClient(
            api_key=self.api_key,
            censored_words=self.censored_words,
            censor_character=self.censor_character,
            server_emotes=self.server_emotes,
        )

        real_prompt = self.botify_input_text(username=author.name, text=prompt)
        response_gen = openai_client.stream_response(
            model=model_id,
            label=label,
            system_prompt=self.system_prompt,
            prompt=real_prompt,
            prev_resp_id=prev_resp_id,
            temperature=temperature,
        )

        embeds: list[discord.Embed] = []

        if show_user_embed:
            embed_user = discord.Embed(color=discord.Color.blurple())
            embed_user.set_author(
                name=f"{author.name}", icon_url=author.display_avatar.url
            )
            embed_user.description = prompt
            embeds.append(embed_user)

        embed_bot = discord.Embed(color=discord.Color.green())
        embed_bot.set_author(name="Drewbot", icon_url=url_bank.drewbot_icon)
        embed_bot.description = "<a:loading:1351145092659937280>"
        embed_bot.set_footer(
            text="Generated with drewbot-model | Tokens: 000 | Cost: $0.0010"
        )
        embeds.append(embed_bot)

        if interaction is not None:
            bot_msg = await interaction.followup.send(embeds=embeds)
        else:
            bot_msg = await channel.send(
                embeds=embeds,
                reference=reply_target.to_reference() if reply_target else None,
                mention_author=False,
            )

        last_update = datetime.now()
        last_content = ""
        full_content = ""
        footer_text = embed_bot.footer.text
        response_id = None
        async for gen_id, content, footer in response_gen:
            response_id = gen_id
            full_content = openai_client.sanitize_for_embed(content)
            footer_text = footer
            if (
                full_content != last_content
                and (datetime.now() - last_update).total_seconds() > 0.5
            ):
                embed_bot.description = (
                    f"{full_content}\n<a:loading:1351145092659937280>"
                )
                embed_bot.set_footer(text=footer_text)
                await self.safe_edit_message(bot_msg, embeds=embeds)
                last_content = full_content
                last_update = datetime.now()

        if full_content:
            embed_bot.description = full_content
            embed_bot.set_footer(text=footer_text)
            await self.safe_edit_message(bot_msg, embeds=embeds)

        if response_id:
            self.active_conversations[(bot_msg.channel.id, bot_msg.id)] = (
                response_id,
                datetime.now(timezone.utc),
                model_id,
                temperature,
                label,
            )

    async def cog_unload(self):
        self.prune_conversations.cancel()

    @tasks.loop(minutes=5)
    async def prune_conversations(self):
        expired = []
        now = datetime.now(timezone.utc)
        for (channel_id, msg_id), (
            resp_id,
            timestamp,
            *_rest,
        ) in self.active_conversations.items():
            if now - timestamp > self.conversation_timeout:
                channel = self.bot.get_channel(channel_id)
                msg = await channel.fetch_message(msg_id)
                if msg and msg.embeds:
                    embed = msg.embeds[-1]
                    footer = embed.footer.text
                    if footer:
                        embed.set_footer(
                            text=f"{footer} | Conversation history has been pruned."
                        )
                    await self.safe_edit_message(msg, embeds=msg.embeds)
                expired.append((channel_id, msg_id))
        for k in expired:
            del self.active_conversations[k]

    async def safe_edit_message(self, msg, **kwargs):
        """
        Edits a message but guarantees we never exceed 1 edit / 0.5s for *this bot*.
        The lock ensures concurrent cogs cooperate.
        """
        async with self.edit_lock:
            now = datetime.now()
            if self.last_edit is not None:
                elapsed = (now - self.last_edit).total_seconds()
                if elapsed < 0.5:
                    await asyncio.sleep(0.5 - elapsed)
            try:
                await msg.edit(**kwargs)
            except (discord.HTTPException, discord.NotFound):
                pass
            self.last_edit = datetime.now()

    async def model_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Return the list of model choices for autocomplete.
        """
        return self.choices

    def botify_input_text(self, username: str, text: str) -> str:
        """
        Given user input text, return the text formatted for Drewbot.
        """
        if username in [self.username, self.username_full]:
            username = "dirtbreadman"
        return f"{username}: {text}\n{self.username}: "

    @app_commands.command(
        name="drewbot", description="Chat with Drewbot. (Patron-only)"
    )
    @app_commands.describe(
        prompt="Your message",
        model="Select a model (default is the first in the list)",
        temperature="Optional: Set the response temperature (0-1.2, default is 0.7)",
    )
    @app_commands.autocomplete(model=model_autocomplete)
    async def chat(
        self,
        interaction: discord.Interaction,
        prompt: str,
        model: str | None = None,
        temperature: discord.app_commands.Range[float, 0.0, 1.2] | None = None,
    ) -> None:
        """
        Sends the user's prompt to OpenAI's Responses API and returns the processed reply.
        The final message is sent via a webhook with the nickname "Drewbot" and a custom avatar.
        A footer is appended on a new line using the "-# " syntax.
        """
        user_is_patron = any(
            user_has_role(interaction, role) for role in self.patron_role_ids
        )
        if not user_is_patron:
            await interaction.response.send_message(
                "D--> You must have the Patron role to use this command.",
                ephemeral=True,
            )
            return

        model_id = model or self.choices[0].value
        all_choices = self.subconfig_data["drewbot_model_choices"]
        chosen = next((c for c in all_choices if c["id"] == model_id), None)
        label = chosen["name"] if chosen else ""
        temp = temperature if temperature is not None else self.base_temperature

        await interaction.response.defer()
        await self._drewbot_respond(
            interaction=interaction,
            channel=interaction.channel,
            author=interaction.user,
            prompt=prompt,
            model_id=model_id,
            label=label,
            temperature=temp,
            show_user_embed=True,
        )


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(DrewBotCog(bot))
