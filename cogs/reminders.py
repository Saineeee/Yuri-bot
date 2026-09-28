"""`/remind` + a 15s sweep loop that DMs due reminders.

Reminders live in MongoDB so they survive restarts. DM delivery falls back
to a channel ping when the user's DMs are closed.
"""

import datetime
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

import utils

log = logging.getLogger(__name__)


# "10m", "2h", "1d", "30s", "1h30m", "2d4h", "90 minutes", ...
_TIME_RE = re.compile(
    r"(?:(\d+)\s*(?:d|days?))?"
    r"(?:(\d+)\s*(?:h|hrs?|hours?))?"
    r"(?:(\d+)\s*(?:m|mins?|minutes?))?"
    r"(?:(\d+)\s*(?:s|secs?|seconds?))?",
    re.IGNORECASE,
)

MAX_REMINDER_DELAY_SECS = 30 * 24 * 3600  # 30 days, matches chat history TTL
MAX_REMINDER_MSG_CHARS = 800
REMINDER_SWEEP_SECS = 15


def parse_time_to_seconds(text: str) -> int | None:
    """Parse '1h30m' (or a bare minute count) into seconds, None if invalid."""
    if not text:
        return None
    text = text.strip().lower()
    # bare integers count as minutes
    if text.isdigit():
        mins = int(text)
        return mins * 60 if mins > 0 else None

    match = _TIME_RE.fullmatch(text)
    if not match:
        return None
    d, h, m, s = (int(g) if g else 0 for g in match.groups())
    total = d * 86400 + h * 3600 + m * 60 + s
    if total <= 0:
        return None
    return total


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._sweep.start()

    def cog_unload(self) -> None:
        self._sweep.cancel()

    @app_commands.command(
        name="remind",
        description="Set a reminder. I'll DM you when it's time.",
    )
    @app_commands.describe(
        time="When to remind you (e.g. '30m', '2h', '1d', '1h30m', or bare minutes)",
        message="What to remind you about (max 800 chars).",
    )
    @app_commands.checks.cooldown(1, 10.0)  # max 1 reminder per 10s per user
    async def remind(
        self,
        interaction: discord.Interaction,
        time: str,
        message: str,
    ) -> None:
        """Set a reminder. Delivery is via DM (falls back to channel ping)."""
        await interaction.response.defer(ephemeral=True)

        secs = parse_time_to_seconds(time)
        if secs is None:
            await interaction.followup.send(
                "i couldn't parse that time bestie 💀 try `30m`, `2h`, `1d`, or `1h30m`.",
                ephemeral=True,
            )
            return

        if secs > MAX_REMINDER_DELAY_SECS:
            await interaction.followup.send(
                f"that's too far in the future 💀 max is 30 days. "
                f"(you asked for {secs // 86400} days)",
                ephemeral=True,
            )
            return

        if len(message) > MAX_REMINDER_MSG_CHARS:
            await interaction.followup.send(
                f"keep the message under {MAX_REMINDER_MSG_CHARS} chars bestie 💀",
                ephemeral=True,
            )
            return

        deliver_at = utils.utcnow() + datetime.timedelta(seconds=secs)

        await self.bot.reminders_collection.insert_one(
            {
                "user_id": interaction.user.id,
                "username": interaction.user.name,
                "channel_id": interaction.channel_id,
                "guild_id": interaction.guild_id,
                "message": message,
                "deliver_at": deliver_at,
                "created_at": utils.utcnow(),
            }
        )

        parts = []
        days, rem = divmod(secs, 86400)
        hrs, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        if days:
            parts.append(f"{days}d")
        if hrs:
            parts.append(f"{hrs}h")
        if mins:
            parts.append(f"{mins}m")
        if secs:
            parts.append(f"{secs}s")
        delay_str = " ".join(parts) or f"{secs}s"

        await interaction.followup.send(
            f"⏰ ok i'll remind you in **{delay_str}**. "
            f"make sure your DMs are open or i'll ping you here instead.",
            ephemeral=True,
        )

    @tasks.loop(seconds=REMINDER_SWEEP_SECS)
    async def _sweep(self) -> None:
        try:
            now = utils.utcnow()
            cursor = self.bot.reminders_collection.find({"deliver_at": {"$lte": now}})
            async for doc in cursor:
                await self._deliver_reminder(doc)
                await self.bot.reminders_collection.delete_one({"_id": doc["_id"]})
        except Exception as e:
            log.warning("reminder sweep error: %s", e)

    @_sweep.before_loop
    async def _before_sweep(self) -> None:
        await self.bot.wait_until_ready()

    async def _deliver_reminder(self, doc: dict) -> None:
        """DM the user; fall back to pinging them in the original channel."""
        user_id = doc["user_id"]
        message = doc.get("message", "")
        # echo back through sanitize: the user wrote it, but mentions must not ping
        safe_msg = utils.sanitize_for_discord(message)
        created_at = doc.get("created_at")

        embed = discord.Embed(
            title="⏰ REMINDER",
            description=safe_msg or "(no message)",
            color=discord.Color.from_rgb(255, 105, 180),
            timestamp=utils.utcnow(),
        )
        if created_at:
            embed.set_footer(text=f"set {created_at.strftime('%b %d, %H:%M')} UTC")

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                log.warning("reminder: user %s no longer exists.", user_id)
                return

        # try DM first, fall back to pinging in the original channel
        try:
            await user.send(embed=embed)
            return
        except discord.Forbidden:
            pass
        except Exception as e:
            log.warning("reminder DM failed for user %s: %s", user_id, e)

        channel_id = doc.get("channel_id")
        if channel_id is None:
            log.warning("reminder: no channel fallback for user %s.", user_id)
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                log.warning("reminder: channel %s no longer accessible.", channel_id)
                return

        try:
            await channel.send(
                content=f"hey <@{user_id}>, you asked me to remind you:",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.Forbidden:
            log.warning("reminder: no permission to post in channel %s.", channel_id)
        except Exception as e:
            log.warning("reminder channel fallback failed: %s", e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reminders(bot))
