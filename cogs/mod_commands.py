import discord
from datetime import datetime, timezone
from collections import Counter

from discord import app_commands
from discord.ext import commands

from classes.discordbot import DiscordBot
from classes.response_bank import url_bank


class ModCommands(commands.Cog, name="mod_commands"):
    """Moderation commands available only to mods and admins."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    # INFO HELP COMMANDS

    @app_commands.guild_only
    @app_commands.command(name="modhelp", description="Show moderation commands.")
    @app_commands.default_permissions(manage_roles=True)
    async def modhelp(self, interaction: discord.Interaction) -> None:
        """Displays moderation commands for users with Manage Roles permission."""
        embed = discord.Embed(
            color=interaction.user.color,
            timestamp=datetime.now(timezone.utc),
            description=(
                "D--> It seems you have asked about the *Homestuck Discord Utility Bot*:tm:.\n"
                "This bot provides moderation, utility, and statistics tracking.\n"
                "For issues, direct your attention to **makin**.\n\n"
                "**Moderation Command List:**"
            ),
        )
        embed.set_author(
            name="Moderation Help message", icon_url=self.bot.user.display_avatar.url
        )
        embed.add_field(name="`modhelp`", value="Show this message.", inline=False)
        embed.add_field(
            name="`modperms`", value="Show your guild permissions.", inline=False
        )
        embed.add_field(
            name="`channel (ban|unban) <user>`",
            value="Mute/unmute a user in a channel.",
            inline=False,
        )
        embed.add_field(
            name="`raidban <user1> [user2 user3 ...]`",
            value="Ban multiple users.",
            inline=False,
        )
        embed.add_field(
            name="`za(warudo|hando)|timeresumes`",
            value="Use dangerous Stand powers to moderate the server.",
            inline=False,
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.guild_only
    @app_commands.command(name="modperms", description="Show your guild permissions.")
    @app_commands.default_permissions(manage_roles=True)
    async def modperms(self, interaction: discord.Interaction) -> None:
        """Lists the user's permissions in the guild."""
        permlist = ", ".join(
            perm for perm, val in interaction.user.guild_permissions if val
        )
        embed = discord.Embed(
            color=interaction.user.color,
            timestamp=datetime.now(timezone.utc),
            description=f"```{permlist}```",
        )
        embed.set_author(name=f"{interaction.user} has the following guild perms:")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # BAN COMMANDS

    @app_commands.guild_only
    @app_commands.command(name="raidban", description="Ban multiple users at once.")
    @app_commands.default_permissions(ban_members=True)
    async def raidban(self, interaction: discord.Interaction, users: str) -> None:
        """
        Ban multiple users by specifying a comma‑separated list (or space‑separated)
        of user IDs or mentions.
        """
        # Defer the interaction so we have time to process.
        await interaction.response.defer(ephemeral=True)

        # Split the input string by commas and/or whitespace.
        user_inputs = [u.strip() for u in users.replace(",", " ").split() if u.strip()]
        if not user_inputs:
            await interaction.followup.send(
                "D--> Provide at least one user to ban.", ephemeral=True
            )
            return

        banned_users = []
        messages = []  # Accumulate error/success messages here.
        for user_str in user_inputs:
            # If the string is in mention format, strip the extra characters.
            if user_str.startswith("<@") and user_str.endswith(">"):
                user_str = user_str.replace("<@", "").replace(">", "").replace("!", "")
            # Try to convert the string to an integer user ID.
            try:
                user_id = int(user_str)
            except ValueError:
                messages.append(
                    f"D--> `{user_str}` is not a valid user ID (mentions/ID only, usernames won't work!)."
                )
                continue

            # Try to get the member from the guild, otherwise fetch the user.
            user = interaction.guild.get_member(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except Exception:
                    messages.append(f"D--> Could not fetch user with ID `{user_id}`.")
                    continue

            try:
                await interaction.guild.ban(
                    user,
                    reason="Banned by anti-raid command.",
                    delete_message_seconds=3600,
                )
                banned_users.append(f"{user} (`{user_id}`)")
            except discord.Forbidden:
                messages.append(
                    f"D--> I cannot ban `{user}` due to permission restrictions."
                )

        if banned_users:
            desc = f"D--> The following users have been STRONGLY e%ecuted:\n{', '.join(banned_users)}"
            messages.append(desc)
            embed = discord.Embed(
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc),
                description=desc,
            )
            embed.set_author(
                name=f"{interaction.user} used raidban in #{interaction.channel}",
                icon_url=interaction.user.display_avatar.url,
            )

            # Log the action if a modlog channel is configured.
            log_channel_id = self.bot.config["bot"].get("modlog_channel_id")
            if log_channel_id:
                log_channel = self.bot.get_channel(log_channel_id)
                if log_channel:
                    await log_channel.send(embed=embed)
        else:
            messages.append("D--> No users were banned.")

        # Send a single followup message with the accumulated responses.
        await interaction.followup.send("\n".join(messages), ephemeral=True)

    # JOJO's Bizarre Adventure Commands

    @app_commands.guild_only
    @app_commands.command(name="zawarudo", description="Stop time in the channel.")
    @app_commands.default_permissions(manage_channels=True)
    async def za_warudo(self, interaction: discord.Interaction) -> None:
        """Locks a channel by removing send permissions."""
        # Determine the channel to use for permission overwrites.
        if isinstance(interaction.channel, discord.Thread):
            target_channel = interaction.channel.parent
        else:
            target_channel = interaction.channel
        perms = target_channel.overwrites_for(interaction.guild.default_role)
        if perms.send_messages is not False:
            embed = discord.Embed(
                color=discord.Color(0xE4E951),
                timestamp=datetime.now(timezone.utc),
                description=f"D--> The time is neigh; your foolish actions shall face STRONG consequences, **#{target_channel}**! It is __***USELESS***__ to resist!",
            )
            embed.set_author(name="D--> 「ザ・ワールド」!!", icon_url=url_bank.dio_icon)
            embed.set_image(url=url_bank.za_warudo)

            perms = target_channel.overwrites_for(interaction.guild.default_role)
            perms.send_messages = False
            await target_channel.set_permissions(
                interaction.guild.default_role, overwrite=perms
            )

            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "Channel is already frozen.", ephemeral=True
            )

    @app_commands.guild_only
    @app_commands.command(name="timeresumes", description="Resume time in the channel.")
    @app_commands.default_permissions(manage_channels=True)
    async def timeresumes(self, interaction: discord.Interaction) -> None:
        """Undoes the channel freeze applied by ZA WARUDO (i.e. restores send_messages permission)."""
        # Determine the channel to use for permission overwrites.
        if isinstance(interaction.channel, discord.Thread):
            target_channel = interaction.channel.parent
        else:
            target_channel = interaction.channel

        default_role = interaction.guild.default_role
        perms = target_channel.overwrites_for(default_role)
        # Check if the channel is currently frozen (i.e. send_messages is explicitly False)
        if perms.send_messages is False:
            perms.update(send_messages=None)
            try:
                await target_channel.set_permissions(default_role, overwrite=perms)
            except discord.HTTPException as e:
                self.bot.log(
                    message=f"Failed to unfreeze channel: {e}",
                    name="BanManager.timeresumes",
                )
                await interaction.response.send_message(
                    "Error: Unable to resume time in this channel.", ephemeral=True
                )
                return

            embed = discord.Embed(
                color=discord.Color(0xE4E951),
                timestamp=datetime.now(timezone.utc),
                description=f"D--> Time has resumed in **#{target_channel}**.",
            )
            embed.set_author(name="D--> 時は動きです。", icon_url=url_bank.dio_icon)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "Channel is not frozen.", ephemeral=True
            )

    @app_commands.guild_only
    @app_commands.command(name="zahando", description="Purge messages in the channel.")
    @app_commands.default_permissions(manage_channels=True)
    async def za_hando(self, interaction: discord.Interaction, limit: int = 10) -> None:
        """Deletes the last X messages in the channel."""
        if limit < 1:
            await interaction.response.send_message(
                "D--> You foolish creature.", ephemeral=True
            )
            return

        # Create the embed to be sent to the channel.
        embed = discord.Embed(
            color=discord.Color(0x303EBB),
            timestamp=datetime.now(timezone.utc),
            description=f"D--> I shall show you my magneighficent STRENGTH, **#{interaction.channel}**!",
        )
        embed.set_author(name="D--> 「ザ・ハンド」!!", icon_url=url_bank.okuyasu_icon)
        embed.set_image(url=url_bank.za_hando)

        # Immediately send the embed as the public response.
        await interaction.response.send_message(embed=embed, ephemeral=False)
        # Retrieve the sent message so we can exclude it from the purge.
        bot_message = await interaction.original_response()

        # Define a check that skips the bot's message.
        def check(msg: discord.Message) -> bool:
            return msg.id != bot_message.id

        # Purge messages, excluding the bot's own message.
        deleted_msgs = await interaction.channel.purge(limit=limit + 1, check=check)

        # Log the purge if needed.
        log_channel_id = self.bot.config["bot"].get("modlog_channel_id")
        if log_channel_id:
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                user_msgs = Counter(msg.author for msg in deleted_msgs)
                log_embed = discord.Embed(
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                    description="\n".join(
                        f"**@{user}**: {count} messages"
                        for user, count in user_msgs.items()
                    ),
                )
                log_embed.set_author(
                    name=f"{interaction.channel} has been purged:",
                    icon_url=url_bank.okuyasu_icon,
                )
                await log_channel.send(embed=log_embed)

    @app_commands.guild_only
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.command(
        name="ignoreplebs",
        description="Toggles the bot accepting commands in a certain channel.",
    )
    async def ignoreplebs(self, interaction: discord.Interaction):
        # Toggle the ignoreplebs category for this channel.
        if isinstance(interaction.channel, discord.Thread):
            target_channel = interaction.channel.parent
        else:
            target_channel = interaction.channel
        added = self.bot.db.toggle_channel_category("ignoreplebs", target_channel.id)
        if added:
            await interaction.response.send_message(
                "D--> I shall listen only to b100 b100ded commands."
            )
        else:
            await interaction.response.send_message(
                "D--> Unfortunately, I must now listen to the lower classes."
            )


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(ModCommands(bot))
