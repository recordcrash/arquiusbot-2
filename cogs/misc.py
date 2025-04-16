import io
import re
import json
import random
import aiohttp
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from classes.response_bank import url_bank, husky_bank
from classes.discordbot import DiscordBot

# Helper function to fetch LaTeX-rendered images.
async def grab_latex(preamble: str, postamble: str, raw_latex: str) -> discord.File | None:
    """Fetches a LaTeX-rendered image and returns it as a discord.File object."""
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            url_bank.latex_parser,
            data={'format': 'png', 'code': preamble + raw_latex + postamble},
        )
        text = await resp.text()
        data = json.loads(text)
        if data.get('status') != 'success':
            return None
        image_resp = await session.get(f'{url_bank.latex_parser}/{data["filename"]}')
        return discord.File(io.BytesIO(await image_resp.read()), filename='latex.png')

# Default LaTeX preamble and postamble.
LATEX_PREAMBLE = (
    r'\documentclass{standalone}\usepackage{color}\usepackage{amsmath}'
    r'\color{white}\begin{document}\begin{math}\displaystyle '
)
LATEX_POSTAMBLE = r'\end{math}\end{document}'


class Misc(commands.Cog, name="misc"):
    """General public commands for the bot."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.subconfig_data: dict = self.bot.config["cogs"][self.__cog_name__.lower()]
        self.latex_channel_ids: list[int] = self.subconfig_data.get("latex_channel_ids", [])

    @app_commands.guild_only
    @app_commands.command(name="flex", description="Request a STRONG flex from the bot.")
    async def flex(self, interaction: discord.Interaction) -> None:
        """Sends a flexing bot image with a STRONG message."""
        embed = discord.Embed(
            color=discord.Color.red(),
            description="D--> It seems you have STRONGLY requested to gaze upon my beautiful body, "
                        "and who am I to refuse such a request?"
        )
        embed.set_author(name="D--> I STRONGLY agree.", icon_url=self.bot.user.display_avatar.url)
        embed.set_image(url=url_bank.flexing_bot)
        await interaction.response.send_message(embed=embed)

    @app_commands.guild_only
    @app_commands.command(name="husky", description="Get a corpulent canine image.")
    async def post_fat_husky(self, interaction: discord.Interaction) -> None:
        """Sends an image of a large husky."""
        embed = discord.Embed(color=discord.Color.red())
        embed.set_author(name="D--> A corpulent canine.", icon_url=husky_bank.icon)
        embed.set_image(url=husky_bank.body)
        await interaction.response.send_message(embed=embed)

    @app_commands.guild_only
    @app_commands.command(name="ping", description="Ping the bot.")
    async def reflect_ping(self, interaction: discord.Interaction) -> None:
        """Pings the bot and responds."""
        await interaction.response.send_message(f'D--> {interaction.user.mention}')

    @app_commands.guild_only
    @app_commands.command(name="roll", description="Roll some dice.")
    @app_commands.describe(args="Dice roll in NdF+M format (e.g., 2d6+1).")
    async def dice_roller(self, interaction: discord.Interaction, args: str) -> None:
        """Rolls dice in NdF+M format (e.g., 2d6+1)."""
        match = re.match(r'(\d+)\s*d\s*(\d+)\s*(?:([-+])\s*(\d+))?$', args.strip())
        if not match:
            await interaction.response.send_message("D--> Use your words, straight from the horse's mouth.",
                                                    ephemeral=True)
            return

        ndice_str, nfaces_str, sign, mod = match.groups()
        ndice = int(ndice_str)
        nfaces = int(nfaces_str)
        # Default missing sign and mod to empty string.
        sign = sign or ''
        mod = mod or ''

        if ndice <= 0 or nfaces <= 0:
            await interaction.response.send_message(
                "D--> That doesn't math very well. I STRONGLY suggest you try again.", ephemeral=True)
            return

        modnum = int(sign + mod) if sign else 0

        rolls = [random.randint(1, nfaces) for _ in range(ndice)]
        result = sum(rolls) + modnum
        msg = f"{interaction.user.mention} **rolled {ndice}d{nfaces}{sign}{mod}:** `({' + '.join(map(str, rolls))})` = **{result}**"
        embed = discord.Embed(
            color=discord.Color.red(),
            description=f'`Min: {min(rolls)}; Max: {max(rolls)}; Mean: {sum(rolls) / ndice:0.2f}; Mode: {max(set(rolls), key=rolls.count)}`'
        )
        embed.set_author(name='Roll Statistics:', icon_url=url_bank.roll_icon)
        await interaction.response.send_message(msg, embed=embed)

    @app_commands.guild_only
    @app_commands.command(name="latex", description="Render a LaTeX equation.")
    @app_commands.describe(latex_code="Your LaTeX equation.")
    async def render_latex(self, interaction: discord.Interaction, latex_code: str) -> None:
        """Generates a LaTeX-rendered image."""
        if isinstance(interaction.channel, discord.Thread):
            target_channel = interaction.channel.parent
        else:
            target_channel = interaction.channel
        if target_channel.id not in self.latex_channel_ids:
            await interaction.response.send_message("D--> You can't generate latex in this channel.", ephemeral=True)
            return

        if not latex_code:
            await interaction.response.send_message("D--> Your latex code is beneighth contempt. Try again.", ephemeral=True)
            return

        await interaction.response.defer()
        image = await grab_latex(LATEX_PREAMBLE, LATEX_POSTAMBLE, latex_code)
        if image is None:
            await interaction.followup.send("D--> Your latex code is invalid. Try again.", ephemeral=True)
            return

        embed = self.bot.create_embed()
        embed.set_author(name=f'Latex render for {interaction.user}', icon_url=url_bank.latex_icon)
        embed.set_image(url="attachment://latex.png")
        await interaction.followup.send(embed=embed, file=image)

    @app_commands.guild_only
    @app_commands.command(name="github", description="Get the bot's GitHub repository link.")
    async def pull_request(self, interaction: discord.Interaction) -> None:
        """Provides the GitHub repository link."""
        embed = discord.Embed(
            color=discord.Color.red(),
            description=(
                "Do you have a friend or a relative who would make a valuable contribution to the bot?\n"
                "In that case, tell them to submit a pull request for their new feature to: "
                "<https://github.com/recordcrash/arquiusbot-2>."
            )
        )
        embed.set_author(name='Got a feature?', icon_url=self.bot.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Misc(bot))
