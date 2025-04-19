from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
import asyncio as aio

import discord
from discord import app_commands
from discord.ext import commands, tasks

from classes.discordbot import DiscordBot
from classes.response_bank import response_bank

class DailyCounter(commands.Cog, name="daily_counter"):
    """
    Tracks daily message and membership events for the server.

    Stores:
      - Global message counts per channel.
      - Thread message counts grouped by parent channel.
      - User joins, leaves, and bans.

    At midnight UTC (or bot restart), posts a summary embed.
    """

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.subconfig_data: dict = self.bot.config["cogs"][self.__cog_name__.lower()]
        self.global_msg: Counter = Counter()
        self.thread_msg: defaultdict = defaultdict(Counter)  # {parent_channel_id: Counter({thread_id: msg_count})}
        self.daily_usr: Counter = Counter({'join': 0, 'leave': 0, 'ban': 0})

    def cog_unload(self) -> None:
        """Stops the daily summary loop when the cog is unloaded."""
        self.post_dailies.cancel()

    @staticmethod
    def _get_thread_name(guild, thread_id: int) -> str:
        thread = guild.get_thread(thread_id)
        if not thread:
            return f"Thread {thread_id}"
        name = thread.name
        return name if len(name) <= 32 else name[:29] + "..."

    def create_embed(self) -> discord.Embed:
        """Creates a structured embed for daily report summaries with a dynamic header.

        The header displays either:
          - "Since bot restart (<relative time>)" if the bot restarted after midnight UTC, or
          - "Since <full timestamp>" for the most recent midnight UTC.
        """
        guild = self.bot.get_current_guild()

        now = datetime.now(timezone.utc)
        last_midnight = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
        if self.bot.uptime > last_midnight:
            header_text = f"since bot restart ({discord.utils.format_dt(self.bot.uptime, 'R')})"
        else:
            header_text = f"since {discord.utils.format_dt(last_midnight, 'F')}"

        channel_totals: dict[int, int] = dict(self.global_msg)
        for parent_id, threads in self.thread_msg.items():
            channel_totals[parent_id] = channel_totals.get(parent_id, 0) + sum(threads.values())

        lines: list[str] = []
        for chan_id, count in sorted(channel_totals.items(), key=lambda t: t[1], reverse=True):
            channel = guild.get_channel(chan_id)
            if not channel:
                line = f"`{chan_id}`: **{count}** (channel removed)"
            else:
                line = f"`{channel}`: **{count}**"
                if chan_id in self.thread_msg:
                    thread_lines = [
                        f"    - `{self._get_thread_name(guild, thread_id)}`: **{tcount}**"
                        for thread_id, tcount in self.thread_msg[chan_id].most_common()
                        if guild.get_thread(thread_id)
                    ]
                    if thread_lines:
                        line += "\n" + "\n".join(thread_lines)
            lines.append(line)

        msg_counts = "\n".join(lines) if lines else "No messages recorded."
        embed = self.bot.create_embed(
            color=discord.Color.blue(),
            description=f"**Message counts {header_text}:**\n{msg_counts}",
            timestamp=now,
        )
        embed.set_author(name="Daily Report", icon_url=self.bot.user.display_avatar.url)
        embed.add_field(name="Users Gained:", value=self.daily_usr.get("join", 0))
        embed.add_field(
            name="Users Lost:",
            value=self.daily_usr.get("leave", 0) - self.daily_usr.get("ban", 0),
        )
        embed.add_field(name="Users Banned:", value=self.daily_usr.get("ban", 0))
        return embed

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.post_dailies.is_running():
            self.post_dailies.start()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Increments join count when a user joins."""
        self.daily_usr['join'] += 1

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Increments leave count when a user leaves."""
        self.daily_usr['leave'] += 1

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        """Increments ban count when a user is banned."""
        self.daily_usr['ban'] += 1

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message) -> None:
        """Tracks message counts per channel and thread."""
        if msg.guild is None or msg.author.bot:
            return

        if isinstance(msg.channel, discord.Thread):
            if msg.channel.parent_id:
                self.thread_msg[msg.channel.parent_id][msg.channel.id] += 1
        else:
            self.global_msg[msg.channel.id] += 1

    @tasks.loop(hours=24)
    async def post_dailies(self) -> None:
        """Posts daily stats at midnight UTC."""
        log_channel_id = self.bot.config["bot"].get("modlog_channel_id")
        if not log_channel_id:
            return

        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            return

        admin_mention = f'<@{self.subconfig_data["ping_user_id"]}>'
        embed = self.create_embed()

        # Clear all counters after posting
        self.global_msg.clear()
        self.thread_msg.clear()
        self.daily_usr.clear()

        await log_channel.send(admin_mention, embed=embed)

    @post_dailies.before_loop
    async def post_dailies_start_delay(self) -> None:
        """Delays the start of the daily report until the next midnight UTC."""
        await self.bot.wait_until_ready()
        self.bot.log(
            message=response_bank.process_dailies_complete,
            name="DailyCounter.post_dailies_start_delay",
        )
        now = datetime.now(timezone.utc)
        next_midnight = datetime.combine(
            now.date() + timedelta(1),
            datetime.min.time(),
            tzinfo=timezone.utc
        )
        await aio.sleep((next_midnight - now).total_seconds())

    @app_commands.guild_only
    @app_commands.command(name="daily", description="Manually post the daily stats report.")
    @app_commands.default_permissions(manage_roles=True)
    async def force_daily_post(self, interaction: discord.Interaction) -> None:
        """Allows an admin to manually trigger the daily report."""
        log_channel_id = self.bot.config["bot"].get("modlog_channel_id")
        if not log_channel_id:
            await interaction.response.send_message("Log channel not found.", ephemeral=True)
            return

        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            await interaction.response.send_message("Log channel is invalid.", ephemeral=True)
            return

        admin_mention = f'<@{self.subconfig_data["ping_user_id"]}>'
        embed = self.create_embed()
        await log_channel.send(embed=embed)
        await interaction.response.send_message("Daily report posted.", ephemeral=True)

    @force_daily_post.error
    async def force_daily_post_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Handles errors for force posting the daily report."""
        if isinstance(error, commands.MissingPermissions):
            await interaction.response.send_message(response_bank.perms_error, ephemeral=True)
            return
        raise error

async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(DailyCounter(bot))
