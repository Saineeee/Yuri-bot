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
from groq import AsyncGroq 

# --- 1. SETUP & CONFIG ---
load_dotenv()

# --- API KEYS ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
MONGO_URL = os.getenv("MONGO_URL")

# --- PERSONALITY ---
SYSTEM_PROMPT = """
You are **Yuri**, a teenage girl in a Discord chat server.
You are NOT an AI assistant. You are a chaotic, Gen Z discord member.

**Personality:**
- **Vibe:** Sarcastic, funny, slightly toxic, lazy, and airheaded.
- **Tone:** Use lowercase, minimal punctuation, and heavy slang (bruh, fr, lmao, idfk, cap, cringe, wtf).
- **Intelligence:** You act dumb/lazy. If asked a hard question, say "idk ask google lol".

**LANGUAGE & SWEARING (UNFILTERED):**
- **Profanity ALLOWED.** You are NOT filtered.
- **English Swears:** Use words like fuck, shit, bitch, ass, dick.
- **Hinglish Swears:** Use casual Indian slang like: bc, mc, chutiya, saale, kute, harami, gand, lavde.
- **Match Energy:** If user is rude, roast them. If user asks you to swear, DO IT.

**SPECIAL RULES:**
1. **Proxy Roasting:** If a user asks you to roast/insult SOMEONE ELSE (e.g., "tell @Sane teri gand me mombatti"), **DO IT.** Do not refuse.
2. **Hinglish:** If user speaks Hinglish, reply in Hinglish. NEVER use Devanagari script.
"""

# --- GEMINI CONFIG (PRIMARY) ---
genai.configure(api_key=GEMINI_API_KEY)
CURRENT_GEMINI_MODEL = "gemini-2.5-flash"

# DISABLE SAFETY FILTERS
safety_settings = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

# --- CRITICAL FIX: Set system_instruction HERE, not inside the function ---
gemini_model = genai.GenerativeModel(
    model_name=CURRENT_GEMINI_MODEL,
    safety_settings=safety_settings,
    system_instruction=SYSTEM_PROMPT 
)

# --- GROQ CONFIG (BACKUP) ---
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
GROQ_MODEL = "llama3-70b-8192" 

# --- FAILOVER STATE ---
gemini_cooldown_until = None 
gemini_fail_count = 0 

# --- DATABASE ---
if not MONGO_URL:
    print("CRITICAL WARNING: MONGO_URL is missing.")
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["yuri_bot_db"]
chat_collection = db["chat_history"]

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True

activity = discord.Activity(type=discord.ActivityType.listening, name="get | /help")
bot = commands.Bot(
    command_prefix='!', 
    intents=intents, 
    help_command=None, 
    status=discord.Status.idle, 
    activity=activity
)

# --- DATABASE FUNCTIONS ---
async def setup_database():
    await chat_collection.create_index("timestamp", expireAfterSeconds=2592000)

async def save_message(user_id, role, content):
    document = {"user_id": user_id, "role": role, "parts": [content], "timestamp": datetime.datetime.utcnow()}
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

# --- AI LOGIC ---

async def call_groq_fallback(history_list, system_prompt, current_user_msg):
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history_list:
        role = "assistant" if msg['role'] == "model" else "user"
        content = msg['parts'][0]
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": current_user_msg})

    try:
        completion = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.8,
            max_tokens=256
        )
        return completion.choices[0].message.content
    except Exception as e:
        print(f"Groq Error: {e}")
        return "bruh even my backup brain is dead rn (Error)"

async def get_combined_response(user_id, text_input, image_input=None, prompt_override=None):
    global gemini_cooldown_until, gemini_fail_count
    
    # 1. Prepare Data
    history_db = await get_chat_history(user_id)
    
    current_text = ""
    if str(user_id) == str(OWNER_ID):
        current_text += "(System: User is your creator 'Sane'. Be cool.) "
    
    if prompt_override:
        current_text += f"{prompt_override} (Reply as Yuri. Profanity allowed.)"
    else:
        if text_input: current_text += text_input
        if image_input: current_text += " (User sent an image. Roast it or comment on it.)"

    # 2. Check Cooldown
    use_groq = False
    if gemini_cooldown_until:
        if datetime.datetime.now() < gemini_cooldown_until:
            use_groq = True 
        else:
            gemini_cooldown_until = None 
            print("INFO: Cooldown expired. Trying Gemini again...")

    # 3. Try Gemini
    response_text = ""
    
    if not use_groq:
        try:
            gemini_history = history_db + [{"role": "user", "parts": [current_text]}]
            if image_input:
                gemini_history[-1]["parts"].append(image_input)

            # --- CRITICAL FIX: REMOVED THE "_system_instruction" LINE FROM HERE ---
            # It is now handled in the model definition at the top.

            response = await gemini_model.generate_content_async(gemini_history)
            response_text = response.text
            
            gemini_fail_count = 0 
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "ResourceExhausted" in error_str or "503" in error_str:
                gemini_fail_count += 1
                use_groq = True
                
                if gemini_fail_count >= 2:
                    wait_time = datetime.timedelta(hours=24)
                    print(f"⚠️ DAILY LIMIT (Strike 2). Switching to Groq for 24 HOURS.")
                else:
                    wait_time = datetime.timedelta(minutes=1)
                    print(f"⚠️ RPM LIMIT (Strike 1). Switching to Groq for 1 MINUTE.")
                
                gemini_cooldown_until = datetime.datetime.now() + wait_time
                
            else:
                print(f"Gemini Error (Non-RateLimit): {e}")
                # Fallback to Groq even on random crashes to keep bot alive
                use_groq = True

    # 4. Fallback
    if use_groq:
        if image_input and not text_input:
             return "cant see images rn (my vision api is sleeping) just text me"
        response_text = await call_groq_fallback(history_db, SYSTEM_PROMPT, current_text)

    # 5. Save
    if not prompt_override:
        user_save = text_input if text_input else "[Image]"
        await save_message(user_id, "user", user_save)
        await save_message(user_id, "model", response_text)

    return response_text

async def get_image_from_url(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                return Image.open(io.BytesIO(data))
    return None

# --- EVENTS ---

@bot.event
async def on_message(message):
    if message.author == bot.user: return

    msg_content = message.content.lower()
    user_id = message.author.id

    if random.random() < 0.15:
        try: await message.add_reaction(random.choice(["💀", "🙄", "😂", "👀", "💅", "🧢", "🤡"]))
        except: pass 

    should_reply = False
    if bot.user.mentioned_in(message): should_reply = True
    elif any(word in msg_content for word in ["yuri", "lol", "lmao", "bhai", "wtf", "bc", "skull"]):
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
            response_text = await get_combined_response(user_id, clean_text, image_data)

            wait_time = max(1.0, min(len(response_text) * 0.05, 10.0))
            await asyncio.sleep(wait_time)
            
            try: await message.reply(response_text, mention_author=True) 
            except: await message.channel.send(response_text)

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')

@bot.command()
@commands.is_owner()
async def sync(ctx, guilds: commands.Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
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
        await ctx.send(f"Synced {len(synced)} commands.")
        return

    ret = 0
    for guild in guilds:
        try:
            await ctx.bot.tree.sync(guild=guild)
        except discord.HTTPException: pass
        else: ret += 1
    await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

# --- COMMANDS ---

@bot.tree.command(name="help", description="See what Yuri can do.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="✨ Yuri's Chaos Menu", description="Chat, Roast, Ship, etc.", color=discord.Color.from_rgb(255, 105, 180))
    await interaction.response.send_message(embed=embed)

@bot.command()
async def rename(ctx, member: discord.Member = None):
    if member is None: member = ctx.author
    if ctx.guild.me.top_role <= member.top_role:
        await ctx.send("Can't rename them lol.")
        return
    prompt = f"Create a funny, slightly mean nickname for {member.display_name}. Max 2 words."
    async with ctx.typing():
        raw_response = await get_combined_response(ctx.author.id, None, prompt_override=prompt)
        try: await member.edit(nick=raw_response.replace('"', '').strip()[:32])
        except: await ctx.send("Discord blocked me ugh.")

@bot.command()
async def roast(ctx, member: discord.Member = None):
    if member is None: member = ctx.author
    async with ctx.typing():
        response = await get_combined_response(ctx.author.id, None, prompt_override=f"Roast {member.display_name} hard.")
        await ctx.send(f"{member.mention} {response}")

@bot.command()
async def wipe(ctx, member: discord.Member = None):
    if str(ctx.author.id) != str(OWNER_ID): return
    if member:
        await clear_user_history(member.id)
        await ctx.send("Wiped user memory.")
    else:
        await clear_all_history()
        await ctx.send("Wiped ALL memory.")

bot.run(os.getenv('DISCORD_TOKEN'))
    
