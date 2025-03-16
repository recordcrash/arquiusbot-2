import os
import random

import discord
from discord import app_commands
from discord.ext import commands

from classes.discordbot import DiscordBot
from classes.response_bank import response_bank

# Helper for file paths.
_addpath = lambda f: os.path.join('texts', f)

_daves = _addpath('daves.txt')
_ryders = _addpath('ryders.txt')
_dungeons = _addpath('dungeons.txt')
_descriptors = _addpath('descriptors.txt')
_figures = _addpath('figures.txt')
_adjectives = _addpath('adjectives.txt')
_groups = _addpath('groups.txt')
_animals = _addpath('animals.txt')
_verbs = _addpath('verbs.txt')
_interlinks = _addpath('interlinked.txt')

DEFAULT_TOTAL = 8

class BullshitGenerator(commands.Cog, name="bullshit_generator"):
    """A collection of humorous name generators."""

    trollgen_cons = "BCDFGHJKLMNPQRSTVXZ"
    trollgen_vows = "AEIOUWY"
    trollgen_weights = ((7, 19), (4, 1), (15, 7), (4, 9), (8, 3), (1, 1))

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    def sample(self, pools, total: int):
        """Returns a zip over randomly sampled, stripped values from each pool."""
        return zip(*(random.sample(list(map(str.strip, pool)), total) for pool in pools))

    @classmethod
    def troll_name(cls) -> str:
        """Generates a random troll name."""
        return ''.join(
            random.choice(random.choices((cls.trollgen_vows, cls.trollgen_cons), w)[0])
            for w in cls.trollgen_weights
        ).capitalize()

    async def send_embed(self, interaction: discord.Interaction, title: str, desc: str) -> None:
        """Sends an embed message."""
        client = self.bot.user
        member = interaction.guild.get_member(client.id)
        color = member.color if member else discord.Color.red()
        embed = discord.Embed(color=color, description=desc)
        embed.set_author(name=title, icon_url=client.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    generate = app_commands.Group(
        name="generate",
        description="Generate random names and titles.",
        guild_only=True,
    )

    @generate.command(name="interlinked", description="Generate a random interlinked phrase.")
    async def interlinked(self, interaction: discord.Interaction) -> None:
        """Generates a random interlinked phrase."""
        with open(_interlinks) as respfile:
            interlinks = random.choice(list(respfile)).strip() + '\n\n**Interlinked.**'
        await self.send_embed(interaction, "Baseline:", interlinks)

    @generate.command(name="ryder", description="Generate MST3K Ryder names.")
    async def generate_ryder(self, interaction: discord.Interaction, total: app_commands.Range[int, 1, 12] = DEFAULT_TOTAL) -> None:
        """Generates MST3K Ryder names."""
        with open(_daves) as daves, open(_ryders) as ryders:
            pools = self.sample((daves, ryders), total)
            embed_desc = '\n'.join(f'{d} {r}' for d, r in pools)
        await self.send_embed(interaction, "Your MST3K Ryder names:", embed_desc)

    @generate.command(name="dungeon", description="Generate fantasy dungeon names.")
    async def generate_dungeon(self, interaction: discord.Interaction, total: app_commands.Range[int, 1, 12] = DEFAULT_TOTAL) -> None:
        """Generates fantasy dungeon names."""
        with open(_dungeons) as dungeons, open(_descriptors) as descriptors:
            pools = self.sample((dungeons, descriptors), total)
            embed_desc = '\n'.join(f'{n} of {d}' for n, d in pools)
        await self.send_embed(interaction, "Your dungeon names:", embed_desc)

    @generate.command(name="group", description="Generate cult or group names.")
    async def generate_cult(self, interaction: discord.Interaction, total: app_commands.Range[int, 1, 12] = DEFAULT_TOTAL) -> None:
        """Generates cult or secret society names."""
        with open(_adjectives) as adjectives, open(_groups) as groups, open(_figures) as figures:
            pools = self.sample((adjectives, groups, figures), total)
            embed_desc = '\n'.join(f'{a} {g} of the {f}' for a, g, f in pools)
        await self.send_embed(interaction, "Your cult names:", embed_desc)

    @generate.command(name="tavern", description="Generate fantasy tavern names.")
    async def generate_tavern(self, interaction: discord.Interaction, total: app_commands.Range[int, 1, 12] = DEFAULT_TOTAL) -> None:
        """Generates fantasy tavern names."""
        with open(_animals) as animals, open(_verbs) as verbs:
            pools = self.sample((verbs, animals), total)
            embed_desc = '\n'.join(f'{v}ing {a}' for v, a in pools)
        await self.send_embed(interaction, "Your tavern names:", embed_desc)

    @generate.command(name="reverse_tavern", description="Generate reverse fantasy tavern names.")
    async def generate_reverse_tavern(self, interaction: discord.Interaction, total: app_commands.Range[int, 1, 12] = DEFAULT_TOTAL) -> None:
        """Generates reversed fantasy tavern names."""
        with open(_animals) as animals, open(_verbs) as verbs:
            pools = self.sample((animals, verbs), total)
            embed_desc = '\n'.join(f'{a}ing {v}' for a, v in pools)
        await self.send_embed(interaction, "Your reverse tavern names:", embed_desc)

    @generate.command(name="actionmovie", description="Generate action movie titles.")
    async def generate_movie(self, interaction: discord.Interaction, total: app_commands.Range[int, 1, 12] = DEFAULT_TOTAL) -> None:
        """Generates action movie titles."""
        with open(_dungeons) as names, open(_descriptors) as descriptors, open(_daves) as firsts, open(_ryders) as lasts:
            pools = self.sample((names, descriptors, firsts, lasts), total)
            embed_desc = '\n'.join(
                f'{f} {l} in the {n} of {d}{"! "[random.randrange(2)]}'
                for n, d, f, l in pools
            )
        await self.send_embed(interaction, "Your sick movie names:", embed_desc)

    @generate.command(name="trollname", description="Generate Homestuck troll names.")
    async def generate_troll_names(self, interaction: discord.Interaction, total: app_commands.Range[int, 1, 12] = 12) -> None:
        """Generates Homestuck-style troll names."""
        names = (self.troll_name() for _ in range(2 * total))
        embed_desc = '\n'.join(f'{f} {l}' for f, l in zip(*[names] * 2))
        await self.send_embed(interaction, "Your troll names:", embed_desc)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(BullshitGenerator(bot))
