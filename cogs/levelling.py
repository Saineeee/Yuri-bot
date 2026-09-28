"""Levelling/XP: /rank, /leaderboard, /rankroles + XP on every message.

XP curve is MEE6-style: level N costs 5*N^2 + 50*N XP.
"""

import asyncio
import contextlib
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import utils

log = logging.getLogger(__name__)


XP_PER_MESSAGE = 15  # base XP per message
XP_COOLDOWN_SECS = 60  # min seconds between XP awards per user
XP_RANGE = 10  # random +/- range on XP per message


def xp_for_level(level: int) -> int:
    """Total XP required to reach *level* (level 0 is free)."""
    if level <= 0:
        return 0
    return 5 * (level**2) + 50 * level


def level_from_xp(xp: int) -> tuple[int, int]:
    """Return (current_level, xp_into_current_level) for a given total XP."""
    if xp <= 0:
        return 0, 0
    level = 0
    while xp >= xp_for_level(level + 1):
        level += 1
    xp_current = xp_for_level(level)
    xp_into_level = xp - xp_current
    return level, xp_into_level


class Levelling(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._xp_cooldowns: dict[int, float] = {}  # user_id -> last_award_time

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Award XP for non-bot, non-command messages."""
        if message.author.bot:
            return
        if message.guild is None:
            return
        if message.content.startswith(self.bot.command_prefix):
            return
        if not message.content.strip():
            return  # skip empty / attachment-only messages

        now = time.monotonic()
        last = self._xp_cooldowns.get(message.author.id, 0)
        if now - last < XP_COOLDOWN_SECS:
            return
        self._xp_cooldowns[message.author.id] = now

        xp_gain = XP_PER_MESSAGE + random.randint(-XP_RANGE, XP_RANGE)

        result = await self.bot.xp_collection.find_one_and_update(
            {"guild_id": message.guild.id, "user_id": message.author.id},
            {
                "$inc": {"xp": xp_gain},
                "$setOnInsert": {
                    "username": message.author.name,
                    "first_message_at": utils.utcnow(),
                },
                "$set": {"last_message_at": utils.utcnow()},
            },
            upsert=True,
            return_document=True,
        )

        await self._check_level_up(message, result)

    async def _check_level_up(self, message: discord.Message, user_doc: dict) -> None:
        """Check if the user leveled up and announce it + grant role rewards."""
        old_xp = user_doc.get("xp", 0) - XP_PER_MESSAGE  # approximate
        new_xp = user_doc.get("xp", 0)
        new_level, _ = level_from_xp(new_xp)
        old_level, _ = level_from_xp(max(old_xp, 0))

        if new_level <= old_level:
            return

        # Level-up announcement (delete after 10s to avoid clutter)
        try:
            embed = discord.Embed(
                title="🎉 Level Up!",
                description=(f"gg <@{message.author.id}> you hit **level {new_level}** fr fr"),
                color=discord.Color.from_rgb(255, 105, 180),
            )
            announcement = await message.channel.send(embed=embed)
            self.bot.loop.create_task(self._delete_after(announcement, 10))
        except Exception as e:
            log.warning("level-up announcement failed: %s", e)

        await self._grant_level_roles(message.guild, message.author, new_level)

    async def _grant_level_roles(
        self, guild: discord.Guild, member: discord.Member, level: int
    ) -> None:
        """Grant any role rewards the user has earned at *level*."""
        config = await self.bot.config_collection.find_one({"guild_id": guild.id})
        if config is None:
            return
        role_rewards = config.get("level_role_rewards", {})
        if not role_rewards:
            return

        # role_rewards is { "5": role_id, "10": role_id, ... }
        roles_to_add = []
        for level_str, role_id in role_rewards.items():
            try:
                threshold = int(level_str)
            except ValueError:
                continue
            if level >= threshold:
                role = guild.get_role(role_id)
                if role is not None and role not in member.roles:
                    roles_to_add.append(role)

        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason=f"reached level {level}")
            except discord.Forbidden:
                log.warning("missing permission to add level roles in guild %s", guild.id)
            except Exception as e:
                log.warning("failed to add level roles: %s", e)

    @staticmethod
    async def _delete_after(message: discord.Message, delay: float) -> None:
        await asyncio.sleep(delay)
        with contextlib.suppress(Exception):
            await message.delete()

    @app_commands.command(
        name="rank",
        description="Show your rank card (level, XP, server position).",
    )
    @app_commands.describe(member="someone else's rank (defaults to you)")
    async def rank(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        """Show a user's rank card."""
        if not interaction.guild:
            await interaction.response.send_message(
                "this only works in a server 💀", ephemeral=True
            )
            return

        target = member or interaction.user
        await interaction.response.defer()

        user_doc = await self.bot.xp_collection.find_one(
            {"guild_id": interaction.guild.id, "user_id": target.id}
        )

        if user_doc is None:
            await interaction.followup.send(f"{target.mention} hasn't sent any messages yet 💀")
            return

        xp = user_doc.get("xp", 0)
        level, xp_into = level_from_xp(xp)
        xp_for_next = xp_for_level(level + 1) - xp_for_level(level)
        progress_pct = int((xp_into / xp_for_next) * 100) if xp_for_next > 0 else 100

        server_rank = (
            await self.bot.xp_collection.count_documents(
                {
                    "guild_id": interaction.guild.id,
                    "xp": {"$gt": xp},
                }
            )
            + 1
        )

        bar_len = 15
        filled = int(bar_len * progress_pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)

        embed = discord.Embed(
            title=f"📊 {target.display_name}'s rank",
            color=discord.Color.from_rgb(255, 105, 180),
            timestamp=utils.utcnow(),
        )
        embed.set_thumbnail(url=target.display_avatar.url if target.display_avatar else None)
        embed.add_field(name="Level", value=f"**{level}**", inline=True)
        embed.add_field(name="Rank", value=f"**#{server_rank}**", inline=True)
        embed.add_field(name="Total XP", value=f"**{xp:,}**", inline=True)
        embed.add_field(
            name=f"Progress to Level {level + 1}",
            value=f"`{bar}` {xp_into}/{xp_for_next} ({progress_pct}%)",
            inline=False,
        )
        embed.set_footer(text="keep chatting to level up bestie")

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="leaderboard",
        description="Top 10 chatters in this server.",
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        """Show the server's top 10 users by XP."""
        if not interaction.guild:
            await interaction.response.send_message(
                "this only works in a server 💀", ephemeral=True
            )
            return

        await interaction.response.defer()

        cursor = (
            self.bot.xp_collection.find({"guild_id": interaction.guild.id}).sort("xp", -1).limit(10)
        )
        docs = [doc async for doc in cursor]

        if not docs:
            await interaction.followup.send("nobody has any XP yet 💀 start chatting bestie")
            return

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, doc in enumerate(docs):
            user_id = doc.get("user_id")
            xp = doc.get("xp", 0)
            level, _ = level_from_xp(xp)
            medal = medals[i] if i < 3 else f"**#{i + 1}**"
            lines.append(f"{medal} <@{user_id}> - **Lvl {level}** • {xp:,} XP")

        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} Leaderboard",
            description="\n".join(lines),
            color=discord.Color.from_rgb(255, 105, 180),
            timestamp=utils.utcnow(),
        )
        embed.set_footer(text="XP is awarded for each message (60s cooldown)")

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="rankroles",
        description="Admin: configure role rewards for leveling up.",
    )
    @app_commands.describe(
        level="the level at which to grant the role (e.g. 5, 10, 20)",
        role="the role to grant",
        action="add or remove this level reward",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
        ]
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def rankroles(
        self,
        interaction: discord.Interaction,
        level: int,
        role: discord.Role,
        action: app_commands.Choice[str],
    ) -> None:
        """Add or remove a level -> role reward."""
        if not interaction.guild:
            await interaction.response.send_message(
                "this only works in a server 💀", ephemeral=True
            )
            return

        if level < 1 or level > 200:
            await interaction.response.send_message(
                "level must be between 1 and 200 💀", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        config = await self.bot.config_collection.find_one({"guild_id": interaction.guild.id})
        rewards = (config or {}).get("level_role_rewards", {})

        if action.value == "add":
            rewards[str(level)] = role.id
            msg = f"✅ level **{level}** now grants {role.mention}"
        else:
            rewards.pop(str(level), None)
            msg = f"✅ removed level **{level}** reward"

        await self.bot.config_collection.update_one(
            {"guild_id": interaction.guild.id},
            {"$set": {"level_role_rewards": rewards}},
            upsert=True,
        )
        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Levelling(bot))
