import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from classes.discordbot import DiscordBot
from classes.response_bank import response_bank

# Multipliers to convert durations to hours.
# 'm' is interpreted as month (732 hours), while 'min' represents one minute (1/60 hours).
_unit_dict = {'h': 1, 'd': 24, 'w': 168, 'm': 732, 'y': 8766, 'min': 1 / 60}

# Upper bound: 100 years  (100 × 8 766 h)
_MAX_BAN_HOURS = 8_766 * 100


class BanManager(commands.Cog, name="ban_manager"):
    """Manages channel bans using roles, with scheduled unbans stored in SQLite."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.bot.log(message=response_bank.process_mutelist, name="BanManager.__init__")
        self.manage_mutelist.start()

    def cog_unload(self) -> None:
        self.manage_mutelist.cancel()

    def _parse_length(self, length: str) -> Optional[float]:
        """
        Turn a duration string (e.g. '3h', '2d', '1min') into hours.

        Returns None for permanent bans ('perma').
        Raises ValueError for bad formats or durations > 100 years.
        """
        length = length.strip()
        if length.lower() == "perma":
            return None

        match = re.match(r"(\d+)(min|[hdwmy])$", length, re.IGNORECASE)
        if not match:
            raise ValueError(
                response_bank.channel_ban_duration_error.format(length=length)
            )

        number = int(match[1])
        unit = match[2].lower()
        hours = number * _unit_dict[unit]

        if hours > _MAX_BAN_HOURS:
            raise ValueError(f"Duration too long: max is 100 years.")
        return hours

    async def _log_mod(self, embed: discord.Embed) -> None:
        """Logs moderation actions in the modlog channel."""
        modlog_channel_id = self.bot.config["bot"].get("modlog_channel_id")
        if modlog_channel_id:
            channel = self.bot.get_channel(modlog_channel_id)
            if channel:
                await channel.send(embed=embed)

    @tasks.loop(minutes=10)
    async def manage_mutelist(self) -> None:
        """Handles scheduled unbans from the database every 10 minutes."""
        now = datetime.now(timezone.utc)
        due_bans = self.bot.db.get_due_scheduled_bans(now)

        guild = self.bot.get_current_guild()
        for ban_id, _, member_id, role_id in due_bans:
            member = guild.get_member(member_id)
            role = guild.get_role(role_id)

            if not member or not role:
                # Optionally log missing members or roles.
                continue

            try:
                await member.remove_roles(role, reason=response_bank.ban_timeout)
            except discord.Forbidden:
                embed = discord.Embed(
                    color=discord.Color.red(),
                    description=response_bank.manage_mutelist_unban_error.format(member=member, role=role),
                )
                await self._log_mod(embed)
            else:
                embed = discord.Embed(
                    color=discord.Color.green(),
                    timestamp=now,
                    description=f'{member.mention} reached timeout for **{role}**.'
                )
                embed.add_field(name='User ID:', value=str(member.id))
                embed.set_author(name=f'{self.bot.user} undid Channel Ban:', icon_url=self.bot.user.display_avatar.url)
                await self._log_mod(embed)

            self.bot.db.delete_scheduled_ban(ban_id)

    @manage_mutelist.before_loop
    async def prepare_mutelist(self) -> None:
        """Waits for bot readiness before starting the loop."""
        await self.bot.wait_until_ready()
        self.bot.log(message=response_bank.process_mutelist_complete, name="BanManager.prepare_mutelist")

    # Define a slash-only command group for channel bans.
    channel = app_commands.Group(
        name="channel",
        description="Manage channel-specific bans.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @channel.command(name="ban", description="Temporarily ban a user in the channel.")
    @app_commands.default_permissions(manage_roles=True)
    async def ban(
            self,
            interaction: discord.Interaction,
            member: discord.Member,
            length: str,
            reason: str = "None specified.",
            memeban: bool = False,
    ) -> None:
        """Applies a channel ban role to a user for a specified duration."""
        # Defer the interaction immediately.
        await interaction.response.defer()
        try:
            duration = self._parse_length(length)
        except ValueError:
            if memeban:
                duration = None
            else:
                await interaction.followup.send(response_bank.channel_ban_duration_error.format(length=length), ephemeral=True)
                return

        if memeban:
            lenstr = "Until further notice." if duration is None else f"{duration} hour(s)."
            await interaction.followup.send(
                response_bank.channel_ban_confirm.format(
                    member=member.mention, until=lenstr, reason=reason
                ),
            )
            return

        if member.id == self.bot.user.id:
            await interaction.followup.send('<:professionalism:1350770886243909702>', ephemeral=True)
            return

        # Determine the channel to use for permission overwrites.
        if isinstance(interaction.channel, discord.Thread):
            target_channel = interaction.channel.parent
        else:
            target_channel = interaction.channel

        # Find the first ban role available in the channel.
        channel_ban_role = next(
            (role for role in interaction.guild.roles if target_channel.overwrites_for(role).pair()[1].send_messages),
            None
        )
        if not channel_ban_role:
            await interaction.followup.send(response_bank.channel_ban_role_error, ephemeral=True)
            return

        try:
            await member.add_roles(channel_ban_role, reason=f'Channel ban: {reason}')
        except discord.Forbidden:
            await interaction.followup.send(f"Error: Could not apply ban role to {member.mention}.", ephemeral=True)
            return

        unban_time = datetime.now(timezone.utc) + timedelta(hours=duration) if duration else None
        if unban_time:
            relative_unban = f"<t:{int(unban_time.timestamp())}:R>"
            self.bot.db.add_scheduled_ban(unban_time, member.id, channel_ban_role.id)
        else:
            relative_unban = "Until further notice"

        embed = discord.Embed(
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
            description=f'{member.mention} has been banned in **#{target_channel}**'
        )
        embed.add_field(name='Duration:', value=f'{duration} hour(s)' if duration else "Until further notice")
        embed.add_field(name='Reason:', value=reason)
        embed.add_field(name='User ID:', value=str(member.id))
        embed.set_author(name=f'{interaction.user} issued channel ban:', icon_url=interaction.user.display_avatar.url)

        await self._log_mod(embed)
        await interaction.followup.send(
            response_bank.channel_ban_confirm.format(member=member.mention, until=relative_unban, reason=reason)
        )

    @channel.command(name="unban", description="Remove a channel ban role from a user.")
    async def unban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "") -> None:
        """Removes a channel ban role from a user."""
        await interaction.response.defer()
        if member.id == self.bot.user.id:
            await interaction.followup.send('<:professionalism:1350770886243909702>', ephemeral=True)
            return

        # Determine the channel to use for permission overwrites.
        if isinstance(interaction.channel, discord.Thread):
            target_channel = interaction.channel.parent
        else:
            target_channel = interaction.channel

        channel_ban_role = next(
            (role for role in member.roles if target_channel.overwrites_for(role).pair()[1].send_messages),
            None
        )
        if not channel_ban_role:
            await interaction.followup.send(response_bank.channel_unban_role_error, ephemeral=True)
            return

        try:
            await member.remove_roles(channel_ban_role, reason=f'Channel unban: {reason}')
        except discord.Forbidden:
            await interaction.followup.send(
                f"Error: Could not remove ban role from {member.mention}.", ephemeral=True
            )
            return

        self.bot.db.remove_scheduled_ban(member.id, channel_ban_role.id)

        embed = discord.Embed(
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
            description=f'{member.mention} has been unbanned in **#{target_channel}**'
        )
        embed.add_field(name='Reason:', value=reason or 'None specified.')
        embed.add_field(name='User ID:', value=str(member.id))
        embed.set_author(name=f'{interaction.user} undid channel ban:', icon_url=interaction.user.display_avatar.url)

        await self._log_mod(embed)
        await interaction.followup.send(
            f"{member.mention} has been unbanned from the channel for reason {reason}.", ephemeral=True
        )

    @app_commands.guild_only
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.command(
        name="banlist", description="Show a list of extant channel bans."
    )
    async def banlist(self, interaction: discord.Interaction) -> None:
        """Shows a list of active scheduled bans."""
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(timezone.utc)
        active_bans = self.bot.db.get_active_scheduled_bans(now)

        if not active_bans:
            await interaction.followup.send(response_bank.no_active_channel_bans, ephemeral=True)
            return

        # Safely unpack ban data (it can easily fail due to too-future dates)
        lines = []
        for ban in active_bans:
            try:
                ban_id, unban_str, member_id, role_id = ban
            except Exception as e:
                continue
            try:
                unban_time = datetime.fromisoformat(unban_str)
                try:
                    # Use discord.utils.format_dt to get a relative timestamp.
                    remaining = discord.utils.format_dt(unban_time, style="R")
                except (OverflowError, OSError, ValueError):
                    remaining = "in the far future"
            except Exception as e:
                remaining = "N/A"

            member = interaction.guild.get_member(member_id)
            role = interaction.guild.get_role(role_id)

            # Use proper Discord mentions if available.
            member_text = member.mention if member else f"<@{member_id}>"
            role_text = role.name if role else f"<@&{role_id}>"
            line = f"Ban ID {ban_id}: {member_text} - {role_text} - Unban {remaining}"
            lines.append(line)

        description = "\n".join(lines)
        embed = self.bot.create_embed(
            title="Active bans",
            description=description,
            color=discord.Color.blue(),
            timestamp=now,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(BanManager(bot))
