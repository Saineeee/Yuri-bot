import discord
from discord.ext import commands
import os
import sys
import asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

# --- PRE-FLIGHT CHECKS ---
REQUIRED_VARS = ["DISCORD_TOKEN", "MONGO_URL", "OWNER_ID", "GEMINI_API_KEY"]
missing_vars = [var for var in REQUIRED_VARS if not os.getenv(var)]
if missing_vars:
    print(f"❌ FATAL: Missing Environment Variables: {', '.join(missing_vars)}")
    sys.exit(1)

try:
    OWNER_ID = int(os.getenv("OWNER_ID"))
except ValueError:
    print("❌ FATAL: OWNER_ID must be an integer.")
    sys.exit(1)

class YuriBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None,
            activity=discord.Activity(type=discord.ActivityType.listening, name="startup...")
        )
        self.owner_id = OWNER_ID

    async def setup_hook(self):
        # Database Setup
        mongo_url = os.getenv("MONGO_URL")
        try:
            # Set timeout to fail fast if DB is unreachable
            self.mongo = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
            # Test connection
            await self.mongo.admin.command('ping')
            self.db = self.mongo["yuri_bot_db"]
            print("✅ MongoDB Connected.")
        except Exception as e:
            print(f"❌ FATAL: MongoDB Connection Failed: {e}")
            sys.exit(1)
            
        # Attach collections globally
        self.chat_collection = self.db["chat_history"]
        self.config_collection = self.db["server_configs"]
        self.crush_collection = self.db["crushes"]
        self.grudge_collection = self.db["grudges"]
        self.feedback_collection = self.db["feedback"]
        
        try:
            # Create Indexes
            await self.chat_collection.create_index("timestamp", expireAfterSeconds=2592000)
            await self.crush_collection.create_index([("lover_id", 1), ("target_id", 1)], unique=True)
            await self.grudge_collection.create_index("user_id", unique=True)
        except Exception as e:
            print(f"⚠️ Index Creation Warning: {e}")

        # Load Cogs
        initial_extensions = ["cogs.ai", "cogs.social", "cogs.admin", "cogs.general"]
        for extension in initial_extensions:
            try:
                await self.load_extension(extension)
            except Exception as e:
                print(f"❌ Failed to load extension {extension}: {e}")

        print("✅ Bot is ready to serve.")

    async def on_ready(self):
        print(f'✨ Logged in as {self.user} (ID: {self.user.id})')
        print('------')

if __name__ == "__main__":
    bot = YuriBot()

    @bot.command()
    @commands.is_owner()
    async def sync(ctx):
        synced = await ctx.bot.tree.sync()
        await ctx.send(f"Synced {len(synced)} slash commands.")

    bot.run(os.getenv('DISCORD_TOKEN'))
