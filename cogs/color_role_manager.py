from typing import Union, Optional

import discord
from discord import app_commands
from discord.ext import commands

from classes.discordbot import DiscordBot
from classes.response_bank import response_bank

EmojiUnion = Union[discord.Emoji, discord.PartialEmoji, str]

class ColorRoleManager(commands.Cog, name="color_role_manager"):
    """
    Manages self-assignable color roles based on predefined messages.
    - Roles are stored in config under "color_role_manager" in "color_role_messages".
    - The bot ensures the correct reactions are present on startup.
    - Users may only have ONE color role at a time.
    """
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.subconfig_data: dict = self.bot.config["cogs"][self.__cog_name__.lower()]
        self.channel_id = self.subconfig_data.get("channel_id")
        self.color_role_messages = self.subconfig_data.get("color_role_messages", {})
        self._ignore_removals = set()  # Track removals triggered by our own action.
        self.bot.log(
            message=f"D--> Loaded {len(self.color_role_messages)} color role messages",
            name="ColorRoleManager.__init__",
        )

    async def _ensure_reactions(self) -> None:
        """Ensure that the correct reactions are present on the configured messages.

        First, remove any reactions not defined in the configuration.
        Then, add any missing reactions.
        Finally, process any valid pending reactions from users (i.e. reactions left while the bot was offline).
        """
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            self.bot.log(
                message="D--> Target channel not found",
                name="ColorRoleManager._ensure_reactions",
            )
            return

        for msg_id, reactions in self.color_role_messages.items():
            try:
                msg = await channel.fetch_message(int(msg_id))
            except discord.NotFound:
                self.bot.log(
                    message=f"D--> Reacted message {msg_id} not found in channel {channel.id}",
                    name="ColorRoleManager._ensure_reactions",
                )
                continue

            # Build a set of expected emoji strings.
            expected_emojis = set()
            for emoji_key in reactions.keys():
                emoji = (
                    self.bot.get_emoji(int(emoji_key))
                    if emoji_key.isdigit()
                    else emoji_key
                )
                expected_emojis.add(str(emoji))

            # Remove any reactions not in the expected set.
            for reaction in msg.reactions:
                if str(reaction.emoji) not in expected_emojis:
                    try:
                        await msg.clear_reaction(reaction.emoji)
                        self.bot.log(
                            message=f"D--> Cleared extra reaction {reaction.emoji} from message {msg_id}",
                            name="ColorRoleManager._ensure_reactions",
                        )
                    except discord.HTTPException as e:
                        self.bot.log(
                            message=f"D--> Failed to clear reaction {reaction.emoji} from message {msg_id}: {e}",
                            name="ColorRoleManager._ensure_reactions",
                        )

            # Re-fetch the message to get the updated reactions.
            msg = await channel.fetch_message(int(msg_id))
            existing_reactions = {str(r.emoji) for r in msg.reactions}

            # Add any missing reactions.
            for emoji_key in reactions.keys():
                emoji = (
                    self.bot.get_emoji(int(emoji_key))
                    if emoji_key.isdigit()
                    else emoji_key
                )
                # Check if emoji resolved to None.
                if emoji is None:
                    self.bot.log(
                        message=f"D--> Emoji for key {emoji_key} resolved to None. Skipping reaction addition for message {msg_id}",
                        name="ColorRoleManager._ensure_reactions"
                    )
                    continue

                expected = str(emoji)
                if expected not in existing_reactions:
                    try:
                        await msg.add_reaction(emoji)
                        self.bot.log(
                            message=f"D--> Added missing reaction {expected} to message {msg_id}",
                            name="ColorRoleManager._ensure_reactions",
                        )
                    except discord.HTTPException as e:
                        self.bot.log(
                            message=f"D--> Failed to add reaction {expected} to message {msg_id}: {e}",
                            name="ColorRoleManager._ensure_reactions",
                        )

            # Process any pending reactions for valid emojis.
            for emoji_key in reactions.keys():
                emoji = (
                    self.bot.get_emoji(int(emoji_key))
                    if emoji_key.isdigit()
                    else emoji_key
                )
                # Locate the corresponding reaction on the message.
                target_reaction = next(
                    (r for r in msg.reactions if str(r.emoji) == str(emoji)), None
                )
                if not target_reaction:
                    continue

                # Process each user's pending reaction.
                async for user in target_reaction.users():
                    if user.id == self.bot.user.id:
                        continue
                    role = self._get_role(msg.id, emoji)
                    if role:
                        self.bot.log(
                            message=f"D--> Processing pending reaction from {user} for role {role}",
                            name="ColorRoleManager._ensure_reactions",
                        )
                        await self._handle_reaction(msg, user, target_reaction)

    async def _purge_color_roles(self, member: discord.Member, new_role: discord.Role) -> None:
        """Remove any color role from the member except for the new one."""
        all_role_ids = {role_id for msg in self.color_role_messages.values() for role_id in msg.values()}
        for role in member.roles:
            if str(role.id) in all_role_ids or role.name in all_role_ids:
                if role.id != new_role.id:
                    try:
                        await member.remove_roles(role, reason=response_bank.role_only_one)
                    except discord.HTTPException:
                        pass

    async def _grant_role(self, member: discord.Member, role: discord.Role) -> bool:
        """Grants the role to the member after purging other color roles."""
        await self._purge_color_roles(member, role)
        try:
            await member.add_roles(role, reason=response_bank.role_reaction_added)
            return True
        except discord.HTTPException:
            return False

    async def _remove_reaction(
        self, msg: discord.Message, reaction: discord.Reaction, member: discord.Member
    ) -> None:
        """Removes a reaction from a message for a given member."""
        key = (msg.id, member.id, str(reaction.emoji))
        self._ignore_removals.add(key)
        try:
            await msg.remove_reaction(reaction.emoji, member)
        except discord.HTTPException:
            pass

    def _get_role(self, message_id: int, emoji: EmojiUnion) -> Optional[discord.Role]:
        """Determines the role corresponding to a given emoji on a message."""
        message_roles = self.color_role_messages.get(str(message_id), {})
        # Determine the key based on emoji type.
        if hasattr(emoji, "id") and emoji.id:
            key = str(emoji.id)
        elif hasattr(emoji, "name") and emoji.name:
            key = str(emoji.name)
        else:
            key = str(emoji)
        role_identifier = message_roles.get(key)
        guild = self.bot.get_current_guild()
        if not role_identifier or not guild:
            return None
        if isinstance(role_identifier, str) and role_identifier.isdigit():
            return guild.get_role(int(role_identifier))
        else:
            return discord.utils.get(guild.roles, name=role_identifier)

    async def _handle_reaction(
        self, msg: discord.Message, member: discord.Member, reaction: discord.Reaction
    ) -> None:
        """Handles a reaction event: if the member already has the role, remove it; otherwise, grant it."""
        role = self._get_role(msg.id, reaction.emoji)
        if not role:
            return

        if role in member.roles:
            try:
                await member.remove_roles(
                    role, reason="User removed color role via reaction"
                )
            except discord.HTTPException:
                pass
            await self._remove_reaction(msg, reaction, member)
        else:
            if await self._grant_role(member, role):
                await self._remove_reaction(msg, reaction, member)

    def _get_member_from_payload(self, payload: discord.RawReactionActionEvent) -> Optional[discord.Member]:
        channel = self.bot.get_channel(payload.channel_id)
        return channel.guild.get_member(payload.user_id) if channel else None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.bot.log(
            message="ColorRoleManager on_ready: ensuring reactions",
            name="ColorRoleManager.on_ready"
        )
        await self._ensure_reactions()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if str(payload.message_id) not in self.color_role_messages or payload.user_id == self.bot.user.id:
            return

        member = self._get_member_from_payload(payload)
        if not member:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            msg = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        # Find the reaction that matches the payload emoji.
        reaction_obj = next(
            (r for r in msg.reactions if str(r.emoji) == str(payload.emoji)), None
        )
        if reaction_obj:
            await self._handle_reaction(msg, member, reaction_obj)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        # If this reaction removal was triggered by our own action, ignore it.
        key = (payload.message_id, payload.user_id, str(payload.emoji))
        if key in self._ignore_removals:
            self._ignore_removals.remove(key)
            return

        if str(payload.message_id) not in self.color_role_messages:
            return

        member = self._get_member_from_payload(payload)
        role = self._get_role(payload.message_id, payload.emoji)
        if member and role:
            try:
                await member.remove_roles(role, reason=response_bank.role_reaction_removed)
            except discord.HTTPException:
                pass

    # MANUAL COMMANDS
    role = app_commands.Group(
        name="role",
        description="Manually add roles to yourself.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @role.command(name="list", description="List all available roles.")
    async def list_roles(self, interaction: discord.Interaction) -> None:
        """Lists all allowed color roles as defined in configuration."""
        allowed = {
            role_id
            for mapping in self.color_role_messages.values()
            for role_id in mapping.values()
        }
        guild = interaction.guild
        roles = []
        for identifier in allowed:
            if isinstance(identifier, str) and identifier.isdigit():
                role = guild.get_role(int(identifier))
            else:
                role = discord.utils.get(guild.roles, name=identifier)
            if role:
                roles.append(role)
        if not roles:
            await interaction.response.send_message(response_bank.role_no_roles, ephemeral=True)
            return
        description = "\n".join(f"`{role.name}` (ID: {role.id})" for role in roles)
        embed = self.bot.create_embed(title=response_bank.role_list, description=description)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @role.command(name="add", description="Add a role to yourself.")
    async def manual_add(self, interaction: discord.Interaction, role: str) -> None:
        """Manually add a color role to yourself."""
        guild = interaction.guild
        try:
            target_role = guild.get_role(int(role))
        except ValueError:
            target_role = discord.utils.get(guild.roles, name=role)
        if not target_role:
            await interaction.response.send_message(
                response_bank.role_not_found, ephemeral=True
            )
            return
        allowed = {
            r for mapping in self.color_role_messages.values() for r in mapping.values()
        }
        for r in interaction.user.roles:
            if r.name in allowed and r.id != target_role.id:
                try:
                    await interaction.user.remove_roles(
                        r, reason=response_bank.role_only_one
                    )
                except discord.HTTPException:
                    pass
        try:
            await interaction.user.add_roles(
                target_role, reason=response_bank.role_manually_added
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                response_bank.role_add_error, ephemeral=True
            )
            return
        await interaction.response.send_message(
            response_bank.role_add_success,
            ephemeral=True,
        )

    @role.command(name="remove", description="Remove a role from yourself.")
    async def manual_remove(self, interaction: discord.Interaction, role: str) -> None:
        """Manually remove a color role from yourself."""
        guild = interaction.guild
        try:
            target_role = guild.get_role(int(role))
        except ValueError:
            target_role = discord.utils.get(guild.roles, name=role)
        if not target_role:
            await interaction.response.send_message(
                response_bank.role_not_found, ephemeral=True
            )
            return
        try:
            await interaction.user.remove_roles(
                target_role, reason=response_bank.role_manually_removed
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                response_bank.role_remove_error, ephemeral=True
            )
            return
        await interaction.response.send_message(
            response_bank.role_remove_success, ephemeral=True,
        )

async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(ColorRoleManager(bot))
