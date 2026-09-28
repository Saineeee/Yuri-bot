"""`/setuproles` + reaction listeners: emoji on the bot's message grants the
mapped role, unreact removes it. Mapping is persisted by message_id."""

import contextlib
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import utils

log = logging.getLogger(__name__)


MAX_ROLE_ENTRIES = 10  # Discord caps a message at 20 reactions


def resolve_role(text: str, guild) -> Optional["discord.Role"]:
    """Resolve a role from a mention like '<@&123>' or a raw ID '123'."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("<@&") and cleaned.endswith(">"):
        cleaned = cleaned[3:-1]
    try:
        role_id = int(cleaned)
    except ValueError:
        return None
    return guild.get_role(role_id)


def parse_entries(text: str, guild):
    """Parse a space-separated list of 'emoji:@role' tokens into (emoji, role) pairs."""
    tokens = text.split()
    out = []
    for token in tokens:
        if ":" not in token:
            continue
        emoji_str, _, role_mention = token.partition(":")
        emoji_str = emoji_str.strip()
        role_mention = role_mention.strip()

        role = resolve_role(role_mention, guild)
        if role is None:
            log.warning("setuproles: couldn't resolve role from '%s'", role_mention)
            continue

        if not emoji_str:
            continue

        out.append((emoji_str, role))
    return out


def reaction_key(emoji) -> str:
    """Stable string key for a reaction emoji: raw char, or '<a?:name:id>' for custom."""
    if emoji.id is None:
        return str(emoji)  # unicode emoji
    return f"<{'a' if emoji.animated else ''}:{emoji.name}:{emoji.id}>"


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setuproles",
        description="Admin: create a reaction-role message in this channel.",
    )
    @app_commands.describe(
        title="Title for the reaction-role embed.",
        description="Optional description / instructions.",
        entries="Up to 10 entries, format: 'emoji:@role emoji:@role ...' "
        "(use a space between entries).",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def setuproles(
        self,
        interaction: discord.Interaction,
        title: str,
        entries: str,
        description: str | None = None,
    ) -> None:
        """Post the reaction-role embed and wire up the reactions."""
        if not interaction.guild:
            await interaction.response.send_message(
                "this only works in a server 💀", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        parsed = self._parse_entries(entries, interaction.guild)

        if not parsed:
            await interaction.followup.send(
                "couldn't parse any role entries 💀 "
                "format: `entries='🔴:@RoleName 🟢:@OtherRole'`",
                ephemeral=True,
            )
            return

        if len(parsed) > MAX_ROLE_ENTRIES:
            await interaction.followup.send(
                f"too many entries 💀 max is {MAX_ROLE_ENTRIES}.",
                ephemeral=True,
            )
            return

        # the bot can only assign roles below its own top role
        bot_member = interaction.guild.me
        skipped = []
        valid = []
        for emoji_str, role in parsed:
            if role >= bot_member.top_role or role.managed:
                skipped.append((emoji_str, role.name))
                continue
            valid.append((emoji_str, role))

        if not valid:
            await interaction.followup.send(
                "no valid roles, all entries are above my top role or are "
                "integration-managed roles I can't assign 💀",
                ephemeral=True,
            )
            return

        # Build the embed
        embed = discord.Embed(
            title=utils.sanitize_for_discord(title)[:256],
            description=(
                utils.sanitize_for_discord(description)
                if description
                else "react below to get a role. unreact to remove it."
            )[:4096],
            color=discord.Color.from_rgb(255, 105, 180),
            timestamp=utils.utcnow(),
        )
        embed.set_footer(text="reaction roles • managed by Yuri")

        # Add a field listing all the role options
        embed.add_field(
            name="Roles",
            value="\n".join(f"{emoji} → {role.mention}" for emoji, role in valid),
            inline=False,
        )

        # Send the message and add reactions
        channel = interaction.channel
        try:
            message = await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(
                "i don't have permission to send messages here 💀",
                ephemeral=True,
            )
            return

        # skip reactions that fail: custom emoji from other servers can't be used
        added_emojis = []
        failed_emojis = []
        for emoji_str, _ in valid:
            try:
                await message.add_reaction(emoji_str)
                added_emojis.append(emoji_str)
            except (discord.Forbidden, discord.HTTPException):
                failed_emojis.append(emoji_str)

        if not added_emojis:
            await interaction.followup.send(
                "couldn't add any reactions 💀 the emoji may be from another server.",
                ephemeral=True,
            )
            with contextlib.suppress(Exception):
                await message.delete()
            return

        # persist message_id -> {emoji_str: role_id}, only for reactions that stuck
        role_map = {}
        for emoji_str, role in valid:
            if emoji_str in added_emojis:
                role_map[emoji_str] = role.id

        await self.bot.reaction_roles_col.insert_one(
            {
                "message_id": message.id,
                "channel_id": channel.id,
                "guild_id": interaction.guild_id,
                "role_map": role_map,
                "created_at": utils.utcnow(),
            }
        )

        summary_lines = [f"✅ posted reaction-role message: {message.jump_url}"]
        if skipped:
            summary_lines.append(
                "⚠️ skipped (above my role / managed): "
                + ", ".join(f"{e} (@{n})" for e, n in skipped)
            )
        if failed_emojis:
            summary_lines.append("⚠️ couldn't add reactions for: " + ", ".join(failed_emojis))

        await interaction.followup.send("\n".join(summary_lines), ephemeral=True)

    def _parse_entries(self, text: str, guild: discord.Guild):
        """Parse the entries string into [(emoji_str, role), ...]."""
        return parse_entries(text, guild)

    @staticmethod
    def _resolve_role(text: str, guild: discord.Guild) -> discord.Role | None:
        """Resolve a role from a mention like '<@&123>' or a raw ID '123'."""
        return resolve_role(text, guild)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Grant a role when a user adds a configured reaction."""
        if payload.member is None or payload.member.bot:
            return  # bot's own reaction or DM reaction

        mapping = await self._get_role_map(payload.message_id)
        if mapping is None:
            return  # not a reaction-role message

        emoji_str = self._reaction_key(payload)
        role_id = mapping.get(emoji_str)
        if role_id is None:
            return  # not one of our configured reactions

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        role = guild.get_role(role_id)
        if role is None:
            return

        try:
            await payload.member.add_roles(role, reason="reaction role")
        except discord.Forbidden:
            log.warning(
                "reaction roles: missing permission to add role %s in guild %s",
                role.name,
                guild.id,
            )
        except discord.HTTPException as e:
            log.warning("reaction roles: failed to add role: %s", e)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """Remove the role when a user removes their reaction."""
        mapping = await self._get_role_map(payload.message_id)
        if mapping is None:
            return

        emoji_str = self._reaction_key(payload)
        role_id = mapping.get(emoji_str)
        if role_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        role = guild.get_role(role_id)
        if role is None:
            return

        try:
            await member.remove_roles(role, reason="reaction role removal")
        except discord.Forbidden:
            log.warning(
                "reaction roles: missing permission to remove role %s in guild %s",
                role.name,
                guild.id,
            )
        except discord.HTTPException as e:
            log.warning("reaction roles: failed to remove role: %s", e)

    async def _get_role_map(self, message_id: int):
        """Return the role_map dict for a message, or None if it's not a RR message."""
        doc = await self.bot.reaction_roles_col.find_one(
            {"message_id": message_id}, {"role_map": 1, "_id": 0}
        )
        if doc is None:
            return None
        return doc.get("role_map")

    @staticmethod
    def _reaction_key(payload: discord.RawReactionActionEvent) -> str:
        """Normalize a reaction's emoji into a stable string key."""
        return reaction_key(payload.emoji)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
