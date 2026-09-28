"""Starboard: messages with enough ⭐ reactions get reposted to a highlight
channel. Star counts are tracked in MongoDB so entries update instead of
reposting. Configured per-guild with /starboard setup."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

import utils

log = logging.getLogger(__name__)


DEFAULT_STAR_THRESHOLD = 5
STAR_EMOJI = "⭐"


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="starboard",
        description="Admin: configure the starboard for this server.",
    )
    @app_commands.describe(
        action="setup or disable the starboard",
        channel="the channel to post starred messages in (required for setup)",
        threshold="how many ⭐ reactions before a message is starred (default 5)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="setup", value="setup"),
            app_commands.Choice(name="disable", value="disable"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def starboard_config(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
        threshold: int | None = None,
    ) -> None:
        """Configure or disable the starboard."""
        if not interaction.guild:
            await interaction.response.send_message(
                "this only works in a server 💀", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if action.value == "setup":
            if channel is None:
                await interaction.followup.send(
                    "you need to specify a channel for setup 💀",
                    ephemeral=True,
                )
                return

            thresh = threshold if threshold and threshold > 0 else DEFAULT_STAR_THRESHOLD

            await self.bot.config_collection.update_one(
                {"guild_id": interaction.guild_id},
                {
                    "$set": {
                        "starboard_channel_id": channel.id,
                        "starboard_threshold": thresh,
                    }
                },
                upsert=True,
            )
            await interaction.followup.send(
                f"✅ starboard set to {channel.mention} with a threshold of " f"**{thresh}** ⭐",
                ephemeral=True,
            )

        elif action.value == "disable":
            await self.bot.config_collection.update_one(
                {"guild_id": interaction.guild_id},
                {
                    "$unset": {
                        "starboard_channel_id": "",
                        "starboard_threshold": "",
                    }
                },
            )
            await interaction.followup.send(
                "✅ starboard disabled for this server.",
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Check if a message has crossed the star threshold."""
        if payload.guild_id is None:
            return
        # Only count ⭐ reactions
        if str(payload.emoji) != STAR_EMOJI:
            return

        await self._check_star_threshold(payload, added=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """Update the starboard message if a ⭐ is removed."""
        if payload.guild_id is None:
            return
        if str(payload.emoji) != STAR_EMOJI:
            return

        await self._check_star_threshold(payload, added=False)

    async def _check_star_threshold(
        self, payload: discord.RawReactionActionEvent, added: bool
    ) -> None:
        """Count ⭐ on the message, post or update the starboard entry."""
        guild_id = payload.guild_id

        config = await self.bot.config_collection.find_one({"guild_id": guild_id})
        if config is None:
            return
        channel_id = config.get("starboard_channel_id")
        threshold = config.get("starboard_threshold", DEFAULT_STAR_THRESHOLD)
        if channel_id is None:
            return  # not configured

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        try:
            source_channel = guild.get_channel(payload.channel_id) or await guild.fetch_channel(
                payload.channel_id
            )
            if source_channel is None:
                return
            source_msg = await source_channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            log.warning("starboard: couldn't fetch source message %s: %s", payload.message_id, e)
            return

        # never star messages in the starboard channel itself (infinite loop guard)
        if payload.channel_id == channel_id:
            return

        star_count = 0
        for reaction in source_msg.reactions:
            if str(reaction.emoji) == STAR_EMOJI:
                star_count = reaction.count
                break

        # Check if we already have a starboard entry for this message
        existing = await self.bot.starboard_col.find_one(
            {"guild_id": guild_id, "message_id": payload.message_id}
        )

        starboard_channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
        if starboard_channel is None:
            log.warning("starboard channel %s no longer exists in guild %s", channel_id, guild_id)
            return

        if star_count >= threshold:
            if existing is None:
                await self._post_starboard_message(starboard_channel, source_msg, star_count)
            else:
                await self._update_starboard_message(starboard_channel, existing, star_count)
        else:
            # dipped below threshold, keep the entry but update the count
            if existing is not None:
                await self._update_starboard_message(starboard_channel, existing, star_count)

    async def _post_starboard_message(
        self,
        starboard_channel: discord.TextChannel,
        source_msg: discord.Message,
        star_count: int,
    ) -> None:
        """Post a new message to the starboard channel and record it in MongoDB."""
        embed = self._build_starboard_embed(source_msg, star_count)

        try:
            starboard_msg = await starboard_channel.send(embed=embed)
        except discord.Forbidden:
            log.warning("starboard: no permission to post in %s", starboard_channel.id)
            return
        except discord.HTTPException as e:
            log.warning("starboard: failed to post: %s", e)
            return

        await self.bot.starboard_col.insert_one(
            {
                "guild_id": source_msg.guild.id,
                "message_id": source_msg.id,
                "channel_id": source_msg.channel.id,
                "author_id": source_msg.author.id,
                "author_name": source_msg.author.display_name,
                "content": source_msg.content[:2000],
                "starboard_message_id": starboard_msg.id,
                "star_count": star_count,
                "created_at": utils.utcnow(),
            }
        )

    async def _update_starboard_message(
        self,
        starboard_channel: discord.TextChannel,
        existing: dict,
        star_count: int,
    ) -> None:
        """Edit the existing starboard message with the new star count."""
        starboard_msg_id = existing.get("starboard_message_id")
        if starboard_msg_id is None:
            return

        try:
            starboard_msg = await starboard_channel.fetch_message(starboard_msg_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            log.warning("starboard: couldn't fetch starboard message %s: %s", starboard_msg_id, e)
            return

        # rebuild the embed; fall back to stored data if the source is gone
        guild = starboard_channel.guild
        try:
            source_channel = guild.get_channel(existing["channel_id"]) or await guild.fetch_channel(
                existing["channel_id"]
            )
            source_msg = await source_channel.fetch_message(existing["message_id"])
        except Exception:
            # source message deleted, use the stored copy
            embed = discord.Embed(
                title=f"{STAR_EMOJI} {star_count}",
                description=existing.get("content", "(message deleted)"),
                color=discord.Color.gold(),
            )
            embed.add_field(
                name="Author",
                value=existing.get("author_name", "unknown"),
                inline=False,
            )
        else:
            embed = self._build_starboard_embed(source_msg, star_count)

        try:
            await starboard_msg.edit(embed=embed)
        except discord.HTTPException as e:
            log.warning("starboard: failed to edit message: %s", e)

        await self.bot.starboard_col.update_one(
            {"_id": existing["_id"]},
            {"$set": {"star_count": star_count}},
        )

    @staticmethod
    def _build_starboard_embed(source_msg: discord.Message, star_count: int) -> discord.Embed:
        """Build the embed for a starboard post."""
        content = utils.sanitize_for_discord(source_msg.content) or "(no text, see attachments)"

        embed = discord.Embed(
            description=content,
            color=discord.Color.gold(),
            timestamp=source_msg.created_at,
        )
        embed.set_author(
            name=source_msg.author.display_name,
            icon_url=(
                source_msg.author.display_avatar.url if source_msg.author.display_avatar else None
            ),
        )
        embed.add_field(
            name="Source",
            value=f"[Jump to message]({source_msg.jump_url})",
            inline=False,
        )
        embed.set_footer(text=f"{STAR_EMOJI} {star_count} • #{source_msg.channel.name}")

        if source_msg.attachments:
            for att in source_msg.attachments:
                if att.content_type and att.content_type.startswith("image/"):
                    embed.set_image(url=att.url)
                    break

        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Starboard(bot))
