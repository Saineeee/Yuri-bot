import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

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
        self.owner_id = int(os.getenv("OWNER_ID"))

    async def setup_hook(self):
        # Database Setup
        mongo_url = os.getenv("MONGO_URL")
        if not mongo_url:
            print("❌ CRITICAL: MONGO_URL missing.")
            return
            
        self.mongo = AsyncIOMotorClient(mongo_url)
        self.db = self.mongo["yuri_bot_db"]
        
        # Attach collections globally
        self.chat_collection = self.db["chat_history"]
        self.config_collection = self.db["server_configs"]
        self.crush_collection = self.db["crushes"]
        self.grudge_collection = self.db["grudges"]
        self.feedback_collection = self.db["feedback"]
        
        # Create Indexes
        await self.chat_collection.create_index("timestamp", expireAfterSeconds=2592000)
        await self.crush_collection.create_index([("lover_id", 1), ("target_id", 1)], unique=True)
        await self.grudge_collection.create_index("user_id", unique=True)

        # Load Cogs
        await self.load_extension("cogs.ai")
        await self.load_extension("cogs.social")
        await self.load_extension("cogs.admin")
        await self.load_extension("cogs.general")
        print("✅ Database Connected & Cogs Loaded.")

    async def on_ready(self):
        print(f'✨ Logged in as {self.user} (ID: {self.user.id})')
        print('------')

bot = YuriBot()

@bot.command()
@commands.is_owner()
async def sync(ctx):
    synced = await ctx.bot.tree.sync()
    await ctx.send(f"Synced {len(synced)} slash commands.")

bot.run(os.getenv('DISCORD_TOKEN'))
