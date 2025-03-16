import os
import random
import discord
from discord import app_commands
from discord.ext import commands, tasks
from itertools import islice
from classes.discordbot import DiscordBot
from classes.rng import ChainProofRHG as ChainProof
from classes.response_bank import url_bank, response_bank

# File paths for stored messages and AI laws
LINKY_MESSAGES_FILE = os.path.join("data", "spat.txt")
AI_LAWS_FILE = os.path.join("texts", "AI_laws.txt")

class Linky(commands.Cog, name="linky"):
    """Handles storing and retrieving messages from a specific user for random responses."""

    __slots__ = ('bot', '_law_total', 'laws', 'subconfig_data', 'user_id')

    linky_rhg = ChainProof(1 / 90)

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.laws = ""
        self.subconfig_data: dict = self.bot.config["cogs"][self.__cog_name__.lower()]
        self.user_id = self.subconfig_data.get("user_id")

        # Ensure data directory exists
        os.makedirs("data", exist_ok=True)

        # Preload law count for 'state laws' command.
        self._law_total = self.count_ai_laws()

    def cog_unload(self) -> None:
        """Stops the periodic law generator when the cog is unloaded."""
        self.gen_laws.cancel()

    def store_message(self, message: str) -> None:
        """Stores a message from the configured user into `data/spat.txt`."""
        with open(LINKY_MESSAGES_FILE, "a", encoding="utf-8") as file:
            file.write(message.strip() + "\n")

    def fetch_random_message(self) -> str:
        """Fetches a random stored message from `data/spat.txt`."""
        try:
            with open(LINKY_MESSAGES_FILE, "r", encoding="utf-8") as file:
                messages = file.readlines()
                return random.choice(messages).strip() if messages else "No stored messages yet."
        except FileNotFoundError:
            return "No stored messages yet."

    def count_ai_laws(self) -> int:
        """Counts the number of lines in `texts/AI_laws.txt`."""
        try:
            with open(AI_LAWS_FILE, "r", encoding="utf-8") as file:
                return sum(1 for _ in file)
        except FileNotFoundError:
            return 0

    def fetch_ai_laws(self, indices: list[int]) -> list[str]:
        try:
            with open(AI_LAWS_FILE, "r", encoding="utf-8") as file:
                # For each index, attempt to get a line; if not found, return an empty string.
                return [next(islice(file, i, None), "").strip() for i in indices]
        except FileNotFoundError:
            return []

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Starts the periodic law generator when the bot is ready."""
        self.bot.log(message=response_bank.linky_on_ready, name="Linky.on_ready")

        self.gen_laws.start()

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message) -> None:
        """Stores messages from the configured user."""
        if msg.author.id == self.user_id:
            self.store_message(msg.clean_content)

    @tasks.loop(minutes=45)
    async def gen_laws(self) -> None:
        """Generates a random set of 'AI laws' from `texts/AI_laws.txt`."""
        law_count = random.choices(range(10), weights=[3, 6, 10, 10, 8, 7, 4, 2, 1, 1])[0]
        if law_count == 0 or self._law_total == 0:
            return

        laws = []
        if law_count > 7:
            extras = random.randint(2, 4)
            laws.extend(random.sample(['0. ', '@#$# ', '@#!# '], extras))
        elif law_count > 3:
            extras = min(law_count - 3, random.choices(range(3), weights=[10, 5, 1])[0])
            laws.extend(random.sample(['0. ', '@#$# ', '@#!# '], extras))
        else:
            extras = 0

        laws = sorted(laws, reverse=True)
        laws.extend(f'{i+1}. ' for i in range(law_count - extras))

        indices = random.sample(range(self._law_total), law_count)
        fetched_laws = self.fetch_ai_laws(indices)

        if fetched_laws:
            for i, text in enumerate(fetched_laws):
                laws[i] += text

            self.laws = '\n\n'.join(laws)


    @app_commands.command(name="linky", description="The Linky speaks!")
    @app_commands.guild_only
    async def respond(
        self, interaction: discord.Interaction, *, query: str = ""
    ) -> None:
        """Retrieves a stored message from LinkyBot and sends it."""
        admin = interaction.guild.get_member(self.user_id)
        embed = discord.Embed(
            color=admin.color if admin else discord.Color.green()
        )
        embed.set_author(
            name=f'{admin.name if admin else "Drew LinkyBot"} says:',
            icon_url=admin.display_avatar.url if admin else url_bank.linky_icon,
        )

        if query.strip().lower() == 'state laws':
            embed.description = self.laws if self.laws else f"I'm afraid I can't do that, {interaction.user.name}."
            await interaction.response.send_message(embed=embed)
            return

        if self.linky_rhg:
            embed.set_image(url=url_bank.linky_rare)
            await interaction.response.send_message(embed=embed)
            return

        message = self.fetch_random_message()
        embed.description = message
        await interaction.response.send_message(embed=embed)

async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Linky(bot))
