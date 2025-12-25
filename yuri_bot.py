import discord
from discord.ext import commands
from discord import app_commands
import os
import random
import asyncio
import aiohttp
import io
import datetime
from typing import Literal, Optional # Added for the sync command
from dotenv import load_dotenv
import google.generativeai as genai
from PIL import Image
from motor.motor_asyncio import AsyncIOMotorClient

# --- 1. SETUP & CONFIG ---
load_dotenv()

# --- MODEL CONFIGURATION ---
CURRENT_MODEL_NAME = "gemini-2.5-flash"

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
OWNER_ID = os.getenv("OWNER_ID") 

# Configure Database (MongoDB)
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    print("CRITICAL WARNING: MONGO_URL is missing. Memory will not work.")

# Connect to DB
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["yuri_bot_db"]
chat_collection = db["chat_history"]

# --- PERSONALITY: YURI ---
SYSTEM_PROMPT = """
You are **Yuri**, a teenage girl in a Discord chat server. You are NOT an AI or a bot.

**Personality:**
- **Vibe:** You are sarcastic, funny, and act a bit "dumb" or airheaded. You are not helpful. You are just here to chat.
- **Tone:** You speak like a real Gen Z person. Use lowercase, lack of punctuation, and slang (bruh, fr, lmao, idfk, cap, cringe).
- **Intelligence:** You act lazy. If someone asks a hard question (like math or history), say you don't know or make a joke about it. Do NOT write essays.
- **Reaction:** You love teasing people. You can be bratty if annoyed, or chill if the vibe is good.

**Strict Language Rules (BILINGUAL):**
1. **English:** If the user speaks English -> Reply in casual English.
2. **Hinglish:** If the user speaks Hindi or Hinglish -> Reply in casual Hinglish (Hindi written with English letters). NEVER use Devanagari script.

**Response Length (Realism Rule):**
- **Match the Energy:** This is the most important rule.
    - If the user sends a short text (e.g., "hi", "lol"), your reply MUST be short (e.g., "sup", "lmao what").
    - If the user sends a long message, story, or rant, you should reply with a longer, thoughtful (but still casual) response.
- **No Robot Speak:** Never use lists or bullet points.

**Specific Triggers:**
- **Images:** If a user sends an image, make a comment on it (roast it or compliment it depending on what it is).
- **Help:** If someone asks for "help" or tries to use you as a tool, tell them to go annoy **@sainnee** instead.

**Example Interactions:**
User: "Hi"
Yuri: "yo"

User (Hinglish): "Kya kar rhi hai?"
Yuri: "kuch nhi bas timepass lol tu bata"

User: "Can you explain quantum physics?"
Yuri: "bruh do i look like einstein to u? google it"

User: "help me"
Yuri: "im not tech support lol go bother @sainnee"

- Use emojis often: 💀, 🙄, 😂, ✨, 🧢.
"""

# Initialize Model
model = genai.GenerativeModel(
    model_name=CURRENT_MODEL_NAME,
    system_instruction=SYSTEM_PROMPT
)

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True

# 1. Define the Custom Activity
activity = discord.Activity(
    type=discord.ActivityType.listening, 
    name="get | /help"
)

# 2. Initialize Bot with Idle Status and Activity
bot = commands.Bot(
    command_prefix='!', 
    intents=intents, 
    help_command=None,
    status=discord.Status.idle,
    activity=activity
)

# --- DATABASE FUNCTIONS ---

async def setup_database():
    """Sets up the 30-day auto-delete rule in MongoDB."""
    await chat_collection.create_index("timestamp", expireAfterSeconds=2592000)
    print("Database: Auto-delete rule (30 days) active.")

async def save_message(user_id, role, content):
    """Saves a message to MongoDB."""
    document = {
        "user_id": user_id,
        "role": role,
        "parts": [content],
        "timestamp": datetime.datetime.utcnow()
    }
    await chat_collection.insert_one(document)

async def get_chat_history(user_id):
    """Fetches the last 20 messages for this user from MongoDB."""
    cursor = chat_collection.find({"user_id": user_id}).sort("timestamp", 1).limit(20)
    history = []
    async for doc in cursor:
        history.append({"role": doc["role"], "parts": doc["parts"]})
    return history

async def clear_user_history(user_id):
    await chat_collection.delete_many({"user_id": user_id})

async def clear_all_history():
    await chat_collection.delete_many({})

# --- HELPER FUNCTIONS ---

async def get_image_from_url(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                return Image.open(io.BytesIO(data))
    return None

async def get_gemini_response(user_id, text_input, image_input=None, prompt_override=None):
    """Sends history + new message to Gemini."""
    try:
        # 1. Fetch History from DB
        history_for_ai = await get_chat_history(user_id)

        # 2. Build Current Message
        current_content = []
        
        # --- IDENTITY INJECTION ---
        if str(user_id) == str(OWNER_ID):
            current_content.append(
                "(System Note: The user sending this message is your creator. "
                "Their username is 'sainnee', but their display name is 'Sane'. "
                "Acknowledge them as your creator, but call them 'Sane' in conversation.)"
            )

        if prompt_override:
            current_content.append(prompt_override)
            current_content.append("(Reply as Yuri in the same language style—English or Hinglish—that the user prefers)")
        else:
            if text_input: current_content.append(text_input)
            if image_input: 
                current_content.append(image_input)
                current_content.append("(User sent an image)")
            if image_input and not text_input:
                current_content.append("Look at this image and make a funny comment (in Hinglish if the vibe fits, otherwise English).")

        # 3. Generate Response
        full_conversation = history_for_ai + [{"role": "user", "parts": current_content}]
        
        # Using the model initialized at top
        response = await model.generate_content_async(full_conversation)
        response_text = response.text

        # 4. Save to DB (Only if it wasn't a special command)
        if not prompt_override:
            user_msg = text_input if text_input else "[Sent an Image]"
            await save_message(user_id, "user", user_msg)
            await save_message(user_id, "model", response_text)

        return response_text

    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "(Error)"

# --- EVENTS ---

@bot.event
async def on_message(message):
    if message.author == bot.user: return

    msg_content = message.content.lower()
    user_id = message.author.id

    # 1. SILENT JUDGING
    if random.random() < 0.15:
        emoji_list = ["💀", "🙄", "😂", "👀", "💅", "🧢", "🤡", "😭"]
        try:
            await message.add_reaction(random.choice(emoji_list))
        except: pass 

    # 2. REPLY LOGIC
    should_reply = False
    if bot.user.mentioned_in(message): should_reply = True
    elif any(word in msg_content for word in ["yuri", "lol", "lmao", "haha", "dead", "skull", "ahi", "bhai", "yaar"]):
        if random.random() < 0.3: should_reply = True
    elif message.attachments:
        if random.random() < 0.5: should_reply = True
    elif random.random() < 0.05: should_reply = True

    if should_reply:
        async with message.channel.typing():
            image_data = None
            if message.attachments:
                attachment = message.attachments[0]
                if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp']):
                    image_data = await get_image_from_url(attachment.url)

            clean_text = message.content.replace(f'<@{bot.user.id}>', 'Yuri').strip()
            response_text = await get_gemini_response(user_id, clean_text, image_data)

            wait_time = max(1.0, min(len(response_text) * 0.06, 12.0))
            await asyncio.sleep(wait_time)
            
            try:
                await message.reply(response_text, mention_author=True)
            except:
                await message.channel.send(response_text)

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    # Note: We do NOT sync here to avoid rate limits. Use !sync command.

# --- SYNC COMMAND (IMPORTANT: RUN THIS TO FIX SLASH COMMANDS) ---
@bot.command()
async def sync(ctx, guilds: commands.Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
    if str(ctx.author.id) != str(OWNER_ID):
        await ctx.send("You are not authorized to sync commands.")
        return

    if not guilds:
        if spec == "~":
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "*":
            ctx.bot.tree.copy_global_to(guild=ctx.guild)
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "^":
            ctx.bot.tree.clear_commands(guild=ctx.guild)
            await ctx.bot.tree.sync(guild=ctx.guild)
            synced = []
        else:
            synced = await ctx.bot.tree.sync()

        await ctx.send(f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild'}.")
        return

    ret = 0
    for guild in guilds:
        try:
            await ctx.bot.tree.sync(guild=guild)
        except discord.HTTPException:
            pass
        else:
            ret += 1

    await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

# --- SLASH COMMANDS ---

@bot.tree.command(name="help", description="See what Yuri can do.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✨ Yuri's Chaos Menu",
        description="Here is everything I can do.",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    embed.add_field(name="💬 Chatting", value="Just tag me or say 'Yuri'. I speak English & Hinglish.", inline=False)
    embed.add_field(name="📸 Vision", value="Upload an image and I'll judge it.", inline=False)
    embed.add_field(name="🔥 !roast @user", value="I will humble them real quick.", inline=True)
    embed.add_field(name="💯 !rate @user", value="I rate their vibe (0-100%).", inline=True)
    embed.add_field(name="❤️ !ship @u1 @u2", value="Toxic love calculator.", inline=True)
    embed.add_field(name="🎱 !ask [question]", value="Sassy 8-Ball answers.", inline=True)
    embed.add_field(name="🏷️ !rename @user", value="I give them a funny new nickname.", inline=True)
    embed.add_field(name="🎲 !truth / !dare", value="Truth or Dare challenges.", inline=True)
    embed.set_footer(text="Yuri Bot | Developed by @sainnee")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="admin_stats", description="Owner Only: See who Yuri remembers.")
async def admin_stats(interaction: discord.Interaction):
    """Shows memory statistics for all users."""
    if str(interaction.user.id) != str(OWNER_ID):
        await interaction.response.send_message("Nice try. You're not @sainnee. 🙄", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        pipeline = [
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        
        results = []
        async for document in chat_collection.aggregate(pipeline):
            user_id = document["_id"]
            count = document["count"]
            
            user = bot.get_user(user_id)
            if user:
                user_label = f"**{user.name}**"
            else:
                try:
                    user = await bot.fetch_user(user_id)
                    user_label = f"**{user.name}**"
                except:
                    user_label = f"Unknown ({user_id})"

            results.append(f"• {user_label}: {count} memories")

        embed = discord.Embed(title="🧠 Yuri's Memory Bank", color=discord.Color.gold())
        if results:
            display_text = "\n".join(results[:20])
            if len(results) > 20: display_text += f"\n\n...and {len(results)-20} others."
            embed.add_field(name="User Activity (Top 20)", value=display_text, inline=False)
        else:
            embed.add_field(name="Status", value="Memory is empty.", inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Database Error: {str(e)}")

# --- TEXT COMMANDS ---

@bot.command()
async def rename(ctx, member: discord.Member = None):
    if member is None: member = ctx.author
    
    if ctx.guild.me.top_role <= member.top_role:
        await ctx.send(f"I want to rename {member.mention}, but they are too powerful (Role Hierarchy). 🙄")
        return

    prompt = (
        f"Create a funny, short, slightly mean nickname for {member.display_name} based on their vibe. "
        "Rules: Max 2-3 words. Use Hinglish if it fits. "
        "CRITICAL: Output ONLY the nickname text. Do not add filler words like 'I think', 'My vote', or 'Nickname:'. "
        "Do not use punctuation."
        "Do not use any emoji."
    )
    
    async with ctx.typing():
        raw_response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        
        new_nickname = raw_response.replace('"', '').replace("'", "").replace(".", "").strip()
        if ":" in new_nickname: new_nickname = new_nickname.split(":")[-1].strip()
        if len(new_nickname) > 32: new_nickname = new_nickname[:32]

        try:
            await member.edit(nick=new_nickname)
            await ctx.send(f"There. Much better. You are now **{new_nickname}**. ✨")
        except: 
            await ctx.send("Ugh, Discord won't let me change it. 🙄")

@bot.command()
async def truth(ctx):
    prompt = "Give a funny, spicy teenage-style Truth question. Can be in English or Hinglish."
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(f"**TRUTH:** {response}")

@bot.command()
async def dare(ctx):
    prompt = "Give a funny, silly Dare for a Discord user. Can be in English or Hinglish."
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(f"**DARE:** {response}")

@bot.command()
async def rate(ctx, member: discord.Member = None):
    if member is None: member = ctx.author
    prompt = f"Rate {member.display_name}'s vibe from 0 to 100%. Give a percentage and a sarcastic reason why."
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(f"{member.mention} {response}")

@bot.command()
async def ship(ctx, member1: discord.Member, member2: discord.Member = None):
    if member2 is None: member2 = ctx.author 
    prompt = f"Calculate romantic compatibility between {member1.display_name} and {member2.display_name}. Give a percentage and a funny, slightly mean prediction."
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(response)

@bot.command()
async def ask(ctx, *, question):
    prompt = f"Answer this yes/no question sassily: {question}"
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(response)

@bot.command()
async def roast(ctx, member: discord.Member = None):
    if member is None: member = ctx.author
    prompt = f"Roast {member.display_name}. Be creative and funny."
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(f"{member.mention} {response}")

# --- OWNER ONLY ---

@bot.command()
async def wipe(ctx, member: discord.Member = None):
    if str(ctx.author.id) != str(OWNER_ID):
        await ctx.send("Nice try. You're not @sainnee. 🙄")
        return
    if member:
        await clear_user_history(member.id)
        await ctx.send(f"Deleted memories of {member.display_name}. Deleted.")
    else:
        await clear_all_history()
        await ctx.send("(Database Wiped)")

# Run
bot.run(os.getenv('DISCORD_TOKEN'))
