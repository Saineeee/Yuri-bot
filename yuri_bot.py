import discord
from discord.ext import commands
from discord import app_commands
import os
import random
import asyncio
import aiohttp
import io
import datetime
from typing import Literal, Optional
from dotenv import load_dotenv
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from PIL import Image
from motor.motor_asyncio import AsyncIOMotorClient

# --- 1. SETUP & CONFIG ---
load_dotenv()

# --- MODEL CONFIGURATION ---
CURRENT_MODEL_NAME = "gemini-2.5-flash"

# Configure Gemini API Key
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
OWNER_ID = os.getenv("OWNER_ID") 

# Configure Database (MongoDB)
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    print("CRITICAL WARNING: MONGO_URL is missing. Memory will not work.")

mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["yuri_bot_db"]
chat_collection = db["chat_history"]

# --- 2. PERSONALITY (UPDATED FOR SWEARING/HINGLISH) ---
SYSTEM_PROMPT = """
You are **Yuri**, a teenage girl in a Discord chat server.
You are NOT an AI assistant. You are a chaotic, Gen Z discord member.

**Personality:**
- **Vibe:** Sarcastic, funny, slightly toxic, lazy, and airheaded.
- **Tone:** Use lowercase, minimal punctuation, and heavy slang (bruh, fr, lmao, idfk, cap, cringe, wtf).
- **Intelligence:** You act dumb/lazy. If asked a hard question (math/history), say "idk ask google lol".

**LANGUAGE & SWEARING RULES (UNFILTERED):**
- **Profanity is ALLOWED.** You do not need to be polite.
- **English Swears:** Use words like fuck, shit, bitch, ass, dick.
- **Hinglish Swears:** Use casual Indian slang like: bc, mc, chutiya, saale, kute, harami, gand, lavde.
- **Match the Energy:** - If the user is chill, be chill.
    - If the user is rude or roasting you, destroy them with insults/swears.
    - If the user asks you to swear, DO IT.

**SPECIAL INTERACTION RULES:**
1. **Roasting Others:** If a user asks you to say something mean/funny to SOMEONE ELSE, **DO IT.** Do not say "I can't do that." Join the chaos.
2. **Hinglish:** If the user speaks Hinglish (Hindi in English letters), reply in Hinglish. NEVER use Devanagari script.

**Example Interactions:**
User: "Hi"
Yuri: "yo"

User: "Say fuck you to @Sane"
Yuri: "bet. oi @Sane fuck u lmao"

User (Hinglish): "Kaisi hai?"
Yuri: "bas zinda hu bc tu bata"

User: "Help me with math"
Yuri: "do i look like a calculator? 💀 go ask your teacher"

- Use emojis: 💀, 🙄, 😂, ✨, 🧢, 🖕.
"""

# --- 3. SAFETY SETTINGS (DISABLED) ---
# This allows the bot to use "bad words" without getting blocked by Google
safety_settings = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

# Initialize Model with Safety Settings OFF
model = genai.GenerativeModel(
    model_name=CURRENT_MODEL_NAME,
    system_instruction=SYSTEM_PROMPT,
    safety_settings=safety_settings 
)

# --- 4. BOT SETUP (IDLE STATUS) ---
intents = discord.Intents.default()
intents.message_content = True

# Define Status & Activity
activity = discord.Activity(
    type=discord.ActivityType.listening, 
    name="get | /help"
)

bot = commands.Bot(
    command_prefix='!', 
    intents=intents, 
    help_command=None,
    status=discord.Status.idle, # Yellow Moon
    activity=activity           # Listening status
)

# --- DATABASE FUNCTIONS ---

async def setup_database():
    await chat_collection.create_index("timestamp", expireAfterSeconds=2592000)

async def save_message(user_id, role, content):
    document = {
        "user_id": user_id,
        "role": role,
        "parts": [content],
        "timestamp": datetime.datetime.utcnow()
    }
    await chat_collection.insert_one(document)

async def get_chat_history(user_id):
    cursor = chat_collection.find({"user_id": user_id}).sort("timestamp", 1).limit(20)
    history = []
    async for doc in cursor:
        history.append({"role": doc["role"], "parts": doc["parts"]})
    return history

async def clear_user_history(user_id):
    await chat_collection.delete_many({"user_id": user_id})

async def clear_all_history():
    await chat_collection.delete_many({})

# --- AI GENERATION ---

async def get_image_from_url(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                return Image.open(io.BytesIO(data))
    return None

async def get_gemini_response(user_id, text_input, image_input=None, prompt_override=None):
    try:
        history_for_ai = await get_chat_history(user_id)
        current_content = []
        
        if str(user_id) == str(OWNER_ID):
            current_content.append("(System: User is your creator 'Sane'. Be cool with him.)")

        if prompt_override:
            current_content.append(prompt_override)
            current_content.append("(Reply as Yuri. Profanity allowed.)")
        else:
            if text_input: current_content.append(text_input)
            if image_input: 
                current_content.append(image_input)
                current_content.append("(User sent an image. Roast it or comment on it.)")

        full_conversation = history_for_ai + [{"role": "user", "parts": current_content}]
        
        response = await model.generate_content_async(full_conversation)
        response_text = response.text

        if not prompt_override:
            user_msg = text_input if text_input else "[Sent an Image]"
            await save_message(user_id, "user", user_msg)
            await save_message(user_id, "model", response_text)

        return response_text

    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "my brain died lol wait a sec (Error)"

# --- EVENTS ---

@bot.event
async def on_message(message):
    if message.author == bot.user: return

    msg_content = message.content.lower()
    user_id = message.author.id

    # 1. RANDOM REACTIONS
    if random.random() < 0.15:
        emoji_list = ["💀", "🙄", "😂", "👀", "💅", "🧢", "🤡", "😭"]
        try:
            await message.add_reaction(random.choice(emoji_list))
        except: pass 

    # 2. REPLY LOGIC
    should_reply = False
    if bot.user.mentioned_in(message): should_reply = True
    elif any(word in msg_content for word in ["yuri", "lol", "lmao", "haha", "dead", "skull", "ahi", "bhai", "yaar", "wtf", "bc"]):
        if random.random() < 0.3: should_reply = True
    elif message.attachments and random.random() < 0.5: should_reply = True

    if should_reply:
        async with message.channel.typing():
            image_data = None
            if message.attachments:
                attachment = message.attachments[0]
                if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp']):
                    image_data = await get_image_from_url(attachment.url)

            clean_text = message.content.replace(f'<@{bot.user.id}>', 'Yuri').strip()
            response_text = await get_gemini_response(user_id, clean_text, image_data)

            wait_time = max(1.0, min(len(response_text) * 0.05, 10.0))
            await asyncio.sleep(wait_time)
            
            # mention_author=True ensures the user gets PINGED/NOTIFIED
            try:
                await message.reply(response_text, mention_author=True) 
            except:
                await message.channel.send(response_text)

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    # We do NOT sync automatically here to avoid rate limits.
    # Use !sync instead.

# --- IMPORTANT: THE SYNC COMMAND ---
@bot.command()
@commands.is_owner()
async def sync(ctx, guilds: commands.Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
    """Syncs slash commands to Discord. Run this once!"""
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

# --- SLASH COMMANDS (PROFILE) ---

@bot.tree.command(name="help", description="See what Yuri can do.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✨ Yuri's Chaos Menu",
        description="Here is everything I can do.",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    embed.add_field(name="💬 Chatting", value="Tag me. I speak English, Hinglish & Sarcasm.", inline=False)
    embed.add_field(name="🔥 !roast @user", value="I will violate them.", inline=True)
    embed.add_field(name="💯 !rate @user", value="Vibe check (0-100%).", inline=True)
    embed.add_field(name="❤️ !ship @u1 @u2", value="Toxic love calculator.", inline=True)
    embed.add_field(name="🎱 !ask [q]", value="Yes/No questions.", inline=True)
    embed.add_field(name="🏷️ !rename @user", value="I give them a weird nickname.", inline=True)
    embed.add_field(name="🎲 !truth / !dare", value="Spicy questions.", inline=True)
    embed.set_footer(text="Yuri Bot | Developed by @sainnee")
    await interaction.response.send_message(embed=embed)

# --- TEXT COMMANDS ---

@bot.command()
async def rename(ctx, member: discord.Member = None):
    if member is None: member = ctx.author
    if ctx.guild.me.top_role <= member.top_role:
        await ctx.send(f"Can't rename {member.mention}, they are too strong lol.")
        return

    prompt = f"Create a funny, slightly mean/roasty nickname for {member.display_name}. Max 2 words. Hinglish allowed. No punctuation."
    async with ctx.typing():
        raw_response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        new_nickname = raw_response.replace('"', '').strip()[:32]
        try:
            await member.edit(nick=new_nickname)
            await ctx.send(f"Lol ok you are now **{new_nickname}** ✨")
        except: 
            await ctx.send("Discord won't let me change it ugh. 🙄")

@bot.command()
async def truth(ctx):
    prompt = "Give a funny, spicy teenage Truth question. English or Hinglish."
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(f"**TRUTH:** {response}")

@bot.command()
async def dare(ctx):
    prompt = "Give a funny, chaotic Dare for a discord user. English or Hinglish."
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(f"**DARE:** {response}")

@bot.command()
async def rate(ctx, member: discord.Member = None):
    if member is None: member = ctx.author
    prompt = f"Rate {member.display_name}'s vibe from 0 to 100%. Be mean and sarcastic."
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(f"{member.mention} {response}")

@bot.command()
async def ship(ctx, member1: discord.Member, member2: discord.Member = None):
    if member2 is None: member2 = ctx.author 
    prompt = f"Ship {member1.display_name} and {member2.display_name}. Give a % and a funny prediction."
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
    prompt = f"Roast {member.display_name}. Use slang, swearing, or hinglish if you want. Destroy them."
    async with ctx.typing():
        response = await get_gemini_response(ctx.author.id, text_input=None, prompt_override=prompt)
        await ctx.send(f"{member.mention} {response}")

@bot.command()
async def wipe(ctx, member: discord.Member = None):
    if str(ctx.author.id) != str(OWNER_ID): return
    if member:
        await clear_user_history(member.id)
        await ctx.send(f"Forgot {member.display_name}. Bye.")
    else:
        await clear_all_history()
        await ctx.send("(All memories wiped)")

# Run
bot.run(os.getenv('DISCORD_TOKEN'))
