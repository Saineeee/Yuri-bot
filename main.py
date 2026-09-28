import asyncio
import datetime
import logging
import logging.config
import os
import pkgutil
import sys

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "discord": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "motor": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}
logging.config.dictConfig(LOGGING_CONFIG)
log = logging.getLogger("yuri")

# Sentry is optional, no-op unless SENTRY_DSN is set
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
            send_default_pii=False,
        )
        log.info("Sentry initialized.")
    except ImportError:
        log.warning("SENTRY_DSN is set but sentry-sdk is not installed.")
    except Exception as e:
        log.warning("Sentry initialization failed: %s", e)

REQUIRED_VARS = ["DISCORD_TOKEN", "MONGO_URL", "OWNER_ID", "GEMINI_API_KEY"]
missing_vars = [var for var in REQUIRED_VARS if not os.getenv(var)]
if missing_vars:
    log.fatal("Missing environment variables: %s", ", ".join(missing_vars))
    sys.exit(1)

try:
    OWNER_ID = int(os.getenv("OWNER_ID"))
except ValueError:
    log.fatal("OWNER_ID must be an integer.")
    sys.exit(1)

# 0 lets discord.py negotiate the shard count itself
SHARD_COUNT = int(os.getenv("SHARD_COUNT", "0")) or None


class YuriBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            activity=discord.Activity(type=discord.ActivityType.listening, name="startup..."),
            shard_count=SHARD_COUNT,
        )
        self.owner_id = OWNER_ID

    async def setup_hook(self):
        mongo_url = os.getenv("MONGO_URL")
        try:
            self.mongo = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
            await self.mongo.admin.command("ping")
            self.db = self.mongo["yuri_bot_db"]
            log.info("MongoDB Connected.")
        except Exception as e:
            log.fatal("MongoDB Connection Failed: %s", e)
            sys.exit(1)

        self.chat_collection = self.db["chat_history"]
        self.config_collection = self.db["server_configs"]
        self.crush_collection = self.db["crushes"]
        self.grudge_collection = self.db["grudges"]
        self.feedback_collection = self.db["feedback"]
        self.privacy_collection = self.db["privacy_prefs"]
        self.pending_verdicts_col = self.db["pending_hotornot"]
        self.reminders_collection = self.db["reminders"]
        self.reaction_roles_col = self.db["reaction_roles"]
        self.memory_dossiers_col = self.db["memory_dossiers"]
        self.starboard_col = self.db["starboard"]
        self.xp_collection = self.db["user_xp"]
        self.image_gen_col = self.db["image_generations"]

        try:
            # chat history: TTL purge after 30 days + a compound index for the
            # usual "find by user, newest first" query
            await self.chat_collection.create_index("timestamp", expireAfterSeconds=2592000)
            await self.chat_collection.create_index([("user_id", 1), ("timestamp", -1)])
            await self.crush_collection.create_index(
                [("lover_id", 1), ("target_id", 1)], unique=True
            )
            await self.grudge_collection.create_index("user_id", unique=True)
            await self.privacy_collection.create_index("user_id", unique=True)
            # sweep loops query these by deliver_at
            await self.pending_verdicts_col.create_index("deliver_at")
            await self.reminders_collection.create_index("deliver_at")
            await self.reminders_collection.create_index("user_id")
            await self.reaction_roles_col.create_index("message_id", unique=True)
            await self.reaction_roles_col.create_index("guild_id")
            await self.memory_dossiers_col.create_index("user_id", unique=True)
            await self.starboard_col.create_index([("guild_id", 1), ("message_id", 1)], unique=True)
            await self.xp_collection.create_index([("guild_id", 1), ("user_id", 1)], unique=True)
            await self.image_gen_col.create_index("user_id")
            await self.image_gen_col.create_index("timestamp", expireAfterSeconds=604800)
        except Exception as e:
            log.warning("Index creation warning: %s", e)

        # auto-discover cogs, prompts.py is just the SYSTEM_PROMPT holder
        import cogs as cogs_pkg

        initial_extensions = [
            f"cogs.{m.name}" for m in pkgutil.iter_modules(cogs_pkg.__path__) if m.name != "prompts"
        ]
        for extension in initial_extensions:
            try:
                await self.load_extension(extension)
            except Exception as e:
                log.error("Failed to load extension %s: %s", extension, e)

        log.info("Bot is ready to serve.")

    async def close(self):
        """Override close to also shut down the shared aiohttp session."""
        try:
            import utils

            await utils.close_session()
        except Exception as e:
            log.warning("Error closing aiohttp session: %s", e)
        await super().close()

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        """Global slash command error handler."""
        error = getattr(error, "original", error)

        cmd = interaction.command.name if interaction.command else "unknown"
        log.error(
            "[SLASH ERROR] /%s by %s (%s): %s: %s",
            cmd,
            interaction.user,
            interaction.user.id,
            type(error).__name__,
            error,
        )

        if isinstance(error, app_commands.MissingPermissions):
            title = "Permission Denied"
            description = "You don't have the required permissions to use this command. Please ask a server administrator for help."

        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(f"`{p}`" for p in error.missing_permissions)
            title = "Bot Missing Permissions"
            description = f"I'm missing the following permissions to run this command: {missing}\n\nPlease ask a server admin to update my role permissions."

        elif isinstance(error, discord.Forbidden):
            title = "Action Blocked by Discord"
            description = "I don't have permission to do that. This is usually a role hierarchy issue, make sure my role is positioned above the target user's role in Server Settings > Roles."

        elif isinstance(error, discord.HTTPException):
            title = "Discord Error"
            description = "Discord had a temporary hiccup while processing this request. Please wait a moment and try again."

        elif isinstance(error, app_commands.CommandOnCooldown):
            title = "Slow Down!"
            description = f"This command is on cooldown. Please try again in **{error.retry_after:.1f} seconds**."

        elif isinstance(error, app_commands.NoPrivateMessage):
            title = "Server Only"
            description = "This command can only be used inside a server, not in DMs."

        elif isinstance(error, app_commands.MissingRole):
            title = "Missing Role"
            description = f"You need the **{error.missing_role}** role to use this command."

        elif isinstance(error, asyncio.TimeoutError):
            title = "Request Timed Out"
            description = "The command took too long to process and timed out. Please try again."

        else:
            title = "Something Went Wrong"
            description = "An unexpected error occurred. If this keeps happening, please report it using `/feedback`."

        embed = discord.Embed(
            title=f"🛠️ {title}",
            description=description,
            color=discord.Color.from_rgb(255, 80, 80),
            timestamp=datetime.datetime.now(datetime.UTC),
        )
        embed.set_footer(text="Developer • Yuri Bot Support")

        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            log.error("[ERROR HANDLER FAILED]: %s", e)

    async def on_ready(self):
        shard_info = ""
        if self.shard_count and self.shard_count > 1:
            shard_info = f" (shards 0-{self.shard_count - 1})"
        log.info("Logged in as %s (ID: %s)%s", self.user, self.user.id, shard_info)
        log.info("In %d guild(s)", len(self.guilds))
        log.info("------")


if __name__ == "__main__":
    bot = YuriBot()

    @bot.command()
    @commands.is_owner()
    async def sync(ctx):
        synced = await ctx.bot.tree.sync()
        await ctx.send(f"Synced {len(synced)} slash commands.")

    bot.run(os.getenv("DISCORD_TOKEN"))
