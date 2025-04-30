from datetime import datetime, timedelta, timezone
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

    def cog_unload(self) -> None:
        """Stops the daily summary loop when the cog is unloaded."""
        self.post_dailies.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.post_dailies.is_running():
            self.post_dailies.start()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Increments join count when a user joins."""
        self.bot.db.increment_daily_user_event("join")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Increments leave count when a user leaves."""
        self.bot.db.increment_daily_user_event("leave")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        """Increments ban count when a user is banned."""
        self.bot.db.increment_daily_user_event("ban")

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message) -> None:
        """Tracks message counts per channel and thread."""
        if msg.guild is None or msg.author.bot:
            return

        if isinstance(msg.channel, discord.Thread):
            # Thread message: increment for parent channel/thread pair
            parent_id = msg.channel.parent_id or msg.channel.id
            self.bot.db.increment_daily_count(parent_id, msg.channel.id)
        else:
            # Regular channel message
            self.bot.db.increment_daily_count(msg.channel.id, None)

    @tasks.loop(hours=24)
    async def post_dailies(self) -> None:
        """Posts daily stats at midnight UTC."""
        # Compute the date for the day that just ended
        now = datetime.now(timezone.utc)
        report_date = (now - timedelta(days=1)).date()

        log_channel_id = self.bot.config["bot"].get("modlog_channel_id")
        if not log_channel_id:
            return

        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            return

        admin_mention = f'<@{self.subconfig_data["ping_user_id"]}>'
        embed = self.create_embed(report_date)

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
            now.date() + timedelta(1), datetime.min.time(), tzinfo=timezone.utc
        )
        await aio.sleep((next_midnight - now).total_seconds())

    @app_commands.guild_only
    @app_commands.command(
        name="daily", description="Manually post the daily stats report."
    )
    @app_commands.default_permissions(manage_roles=True)
    async def force_daily_post(self, interaction: discord.Interaction) -> None:
        """Allows an admin to manually trigger the daily report."""
        # Always report on the previous calendar day
        report_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

        log_channel_id = self.bot.config["bot"].get("modlog_channel_id")
        if not log_channel_id:
            await interaction.response.send_message(
                "Log channel not found.", ephemeral=True
            )
            return

        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            await interaction.response.send_message(
                "Log channel is invalid.", ephemeral=True
            )
            return

        embed = self.create_embed(report_date)
        await log_channel.send(embed=embed)
        await interaction.response.send_message("Daily report posted.", ephemeral=True)

    @force_daily_post.error
    async def force_daily_post_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        """Handles errors for force posting the daily report."""
        if isinstance(error, commands.MissingPermissions):
            await interaction.response.send_message(
                response_bank.perms_error, ephemeral=True
            )
            return
        raise error

    def create_embed(self, report_date: datetime.date) -> discord.Embed:
        """Creates a structured embed for daily report summaries for a given date."""
        guild = self.bot.get_current_guild()
        now = datetime.now(timezone.utc)

        # Header shows the calendar date being reported
        header_text = report_date.isoformat()

        # Fetch message counts and user events for that date
        counts = self.bot.db.get_daily_counts(report_date)
        events = self.bot.db.get_daily_user_events(report_date)

        # Aggregate totals per channel and per thread
        channel_totals: dict[int, int] = {}
        thread_msg: dict[int, dict[int, int]] = {}
        for chan_id, thr_id, cnt in counts:
            channel_totals[chan_id] = channel_totals.get(chan_id, 0) + cnt
            if thr_id is not None:
                thread_msg.setdefault(chan_id, {})[thr_id] = cnt

        # Build the per-channel lines
        lines: list[str] = []
        for chan_id, count in sorted(
            channel_totals.items(), key=lambda t: t[1], reverse=True
        ):
            channel = guild.get_channel(chan_id)
            name = f"`{chan_id}`" if not channel else f"`{channel.name}`"
            line = f"{name}: **{count}**"
            # Add threads under the channel if any
            if chan_id in thread_msg:
                for thread_id, tcount in thread_msg[chan_id].items():
                    thread = guild.get_thread(thread_id)
                    tname = thread.name if thread else str(thread_id)
                    line += f"\n    • `{tname}`: **{tcount}**"
            lines.append(line)

        total_messages = sum(channel_totals.values())
        if total_messages:
            lines.append(f"`All channels`: **{total_messages}**")

        msg_counts = "\n".join(lines) if lines else "No messages recorded."

        embed = self.bot.create_embed(
            color=discord.Color.blue(),
            description=f"**Message counts for {header_text}:**\n{msg_counts}",
            timestamp=now,
        )
        embed.set_author(name="Daily Report", icon_url=self.bot.user.display_avatar.url)
        embed.add_field(name="Total Messages:", value=str(total_messages))
        embed.add_field(name="Current Users:", value=str(guild.member_count))
        embed.add_field(name="Users Gained:", value=str(events.get("join", 0)))
        # leave minus ban = net leaves
        embed.add_field(
            name="Users Lost:", value=str(events.get("leave", 0) - events.get("ban", 0))
        )
        embed.add_field(name="Users Banned:", value=str(events.get("ban", 0)))
        return embed


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(DailyCounter(bot))
