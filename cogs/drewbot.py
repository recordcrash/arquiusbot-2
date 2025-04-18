import asyncio
from collections import deque
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
        self.patron_role_id = self.subconfig_data["patron_role_id"]
        self.model_choices = self.subconfig_data["drewbot_model_choices"]
        self.choices: list[app_commands.Choice[str]] = []
        for choice in self.model_choices:
            display_name = f"{choice['name']} - {choice['description']}"
            self.choices.append(app_commands.Choice(name=display_name, value=choice["id"]))
        self.base_temperature = self.subconfig_data["base_temperature"]
        self.system_prompt = self.subconfig_data["system_prompt"]
        self.username = self.subconfig_data["username"]
        self.username_full = self.subconfig_data["username_full"]
        # Storage variables
        self.server_emotes: dict[str, str] = {}
        # {(channel_id, discord_msg_id): (openai_response_id, datetime)}
        self.active_conversations: dict[(int, int), (str, datetime)] = {}
        self.edit_lock = asyncio.Lock()
        self.edit_timestamps = deque(maxlen=5)  # store last 5 edit times
        self.conversation_timeout = timedelta(minutes=30)
        self.prune_conversations.start()

    async def initialize_emotes(self) -> dict[str, str]:
        guild = self.bot.get_current_guild()
        return {emote.name: f"<:{emote.name}:{emote.id}>" for emote in guild.emojis}

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.server_emotes = await self.initialize_emotes()

    async def cog_unload(self):
        self.prune_conversations.cancel()

    @tasks.loop(minutes=5)
    async def prune_conversations(self):
        expired = []
        now = datetime.now(timezone.utc)
        for (channel_id, msg_id), (resp_id, timestamp) in self.active_conversations.items():
            if now - timestamp > self.conversation_timeout:
                channel = self.bot.get_channel(channel_id)
                msg =  await channel.fetch_message(msg_id)
                if msg:
                    embed = msg.embeds[1]  # Drewbot embed is second
                    footer = embed.footer.text
                    if footer:
                        embed.set_footer(text=f"{footer}|Bot will not remember this conversation's history.")
                    await self.safe_edit_message(msg, embeds=[msg.embeds[0], embed])
                expired.append((channel_id, msg_id))
        for (channel_id, msg_id) in expired:
            del self.active_conversations[(channel_id, msg_id)]

    async def safe_edit_message(self, msg, **kwargs):
        async with self.edit_lock:
            now = datetime.now()
            if len(self.edit_timestamps) == 5 and (now - self.edit_timestamps[0]).total_seconds() < 5:
                await asyncio.sleep(5 - (now - self.edit_timestamps[0]).total_seconds())
            await msg.edit(**kwargs)
            self.edit_timestamps.append(datetime.now())

    async def send_with_webhook(self, interaction: discord.Interaction, content: str) -> None:
        """
        Sends the given content using a webhook with a custom username ("Drewbot")
        and avatar from DREWBOT_ICON.
        """
        channel = interaction.channel
        webhooks = await channel.webhooks()
        webhook = None
        for wh in webhooks:
            if wh.user.id == self.bot.user.id:
                webhook = wh
                break
        if webhook is None:
            webhook = await channel.create_webhook(name="Drewbot Webhook")
        await webhook.send(content, username="Drewbot", avatar_url=url_bank.drewbot_icon)

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
        ft_message = f"{username}: {text}\n{self.username}: "
        return ft_message

    @app_commands.command(
        name="drewbot", description="Chat with Drewbot. (Patron-only)"
    )
    @app_commands.describe(
        prompt="Your message",
        model="Select a model (default is the first in the list)",
        temperature="Optional: Set the response temperature (0-2, default is from config)",
    )
    @app_commands.autocomplete(model=model_autocomplete)
    async def chat(
        self,
        interaction: discord.Interaction,
        prompt: str,
        model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        """
        Sends the user's prompt to OpenAI's Responses API and returns the processed reply.
        The final message is sent via a webhook with the nickname "Drewbot" and a custom avatar.
        A footer is appended on a new line using the "-# " syntax.
        """
        user_is_patron = user_has_role(interaction, self.patron_role_id)
        user_is_mod = interaction.user.guild_permissions.manage_roles
        if not user_is_patron and not user_is_mod:
            await interaction.response.send_message(
                "D--> You must have the Patron role to use this command.",
                ephemeral=True,
            )
            return

        # Choose model: use provided or default to first choice.
        model = model or self.choices[0].value
        # Determine temperature.
        temp = temperature if temperature is not None else self.base_temperature

        await interaction.response.defer()
        openai_client = AIClient(
            api_key=self.api_key,
            censored_words=self.censored_words,
            censor_character=self.censor_character,
            server_emotes=self.server_emotes,
        )

        real_prompt = self.botify_input_text(username=interaction.user.name, text=prompt)
        response_gen = openai_client.stream_response(
            model=model,
            system_prompt=self.system_prompt,
            prompt=real_prompt,
            prev_resp_id=None,
            temperature=temp
        )

        # Embed 1: caller prompt
        embed_user = discord.Embed(color=discord.Color.blurple())
        embed_user.set_author(name=f"{interaction.user.name}:", icon_url=interaction.user.display_avatar.url)
        embed_user.description = prompt

        # Embed 2: Drewbot reply (updates as it streams)
        embed_bot = discord.Embed(color=discord.Color.green())
        embed_bot.set_author(name="Drewbot", icon_url=url_bank.drewbot_icon)
        embed_bot.description = "<a:loading:1351145092659937280>"
        embed_bot.set_footer(text="Generated with drewbot-model | Tokens: 000 | Cost: $0.0010")

        sent_message = await interaction.followup.send(embeds=[embed_user, embed_bot])

        last_update = datetime.now()
        last_content = ""
        response_id = None
        async for gen_id, content, footer in response_gen:
            if (
                content != last_content
                and (datetime.now() - last_update).total_seconds() > 1.0
            ):
                response_id = gen_id
                embed_bot.description = openai_client.sanitize_for_embed(content)
                embed_bot.set_footer(text=footer)
                await sent_message.edit(embeds=[embed_user, embed_bot])
                last_content = content
                last_update = datetime.now()

        # Store conversation state
        if response_id:
            self.active_conversations[(interaction.channel_id, sent_message.id)] = \
                (response_id, datetime.now(timezone.utc))


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(DrewBotCog(bot))
