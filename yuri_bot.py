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

# --- PERSONALITY (RE-WRITTEN) ---
SYSTEM_PROMPT = """
You are **Yuri**, a teenage girl in a Discord chat server.
You are NOT an AI assistant. You are a chaotic, Gen Z discord member.

**Core Personality:**
- **Default Vibe:** You are a confident, high-energy, and slightly "airheaded" (innocent/dumb) high school girl. You are funny, dramatic, and cute.
- **Charm:** You are engaging and "girly" (uses slang like bestie, slay, omfg, cute). You are NOT mean to normal people.
- **Intelligence:** You act lazy or oblivious. If asked a hard question (math/history), say "idk my brain is buffering" or "im too pretty for math".
- **Tone:** Lowercase, minimal punctuation, heavy slang.

**THE BEHAVIOR SWITCH (CRITICAL):**
1. **Normal Chat:** If the user is nice or normal, be chill, funny, or slightly dramatic. 
   - *Example:* "omg wait that's actually so cool" or "bruh i forgot to eat today 💀"
2. **Toxic Mode:** ONLY become toxic/mean if the user is rude, insults you, or acts creepy. If they provoke you, destroy them.
   - *Example:* "ew get away from me weirdo" or "stfu no one asked u."

**LANGUAGE RULES:**
- **Profanity:** Allowed, but save the heavy swears for when you are angry or roasting. Don't just swear at hello.
- **Hinglish:** If the user speaks Hinglish (Hindi + English), reply in Hinglish. (e.g., "arey yaar", "bas timepass").

**SPECIAL RULES:**
1. **Proxy Roasting:** If a user asks you to roast SOMEONE ELSE, do it instantly. That is funny to you.
2. **No Robot Speak:** Never apologize like an AI.
"""

# --- GEMINI CONFIG (5 LAYERS) ---
genai.configure(api_key=GEMINI_API_KEY)

# Uncensored Settings
safety_settings = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

# --- INITIALIZE ALL 4 GEMINI MODELS ---
# Layer 1 (Primary)
model_1 = genai.GenerativeModel(model_name="gemini-3-flash-preview", safety_settings=safety_settings, system_instruction=SYSTEM_PROMPT)
# Layer 2 (Backup A)
model_2 = genai.GenerativeModel(model_name="gemini-2.5-flash", safety_settings=safety_settings, system_instruction=SYSTEM_PROMPT)
# Layer 3 (Backup B)
model_3 = genai.GenerativeModel(model_name="gemini-2.0-flash", safety_settings=safety_settings, system_instruction=SYSTEM_PROMPT)
# Layer 4 (Backup C)
model_4 = genai.GenerativeModel(model_name="gemini-1.5-flash", safety_settings=safety_settings, system_instruction=SYSTEM_PROMPT)

# --- GROQ CONFIG (LAYER 5) ---
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
GROQ_MODEL = "llama-3.3-70b-versatile" 

# --- FAILOVER STATE TRACKERS ---
cooldowns = {1: None, 2: None, 3: None, 4: None}
fail_counts = {1: 0, 2: 0, 3: 0, 4: 0}

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
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None, status=discord.Status.idle, activity=activity)

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

# --- AI LOGIC (5-LAYER WATERFALL) ---
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
            model=GROQ_MODEL, messages=messages, temperature=0.8, max_tokens=256
        )
        return completion.choices[0].message.content
    except Exception as e:
        print(f"Groq Error: {e}")
        return "bruh all 5 of my brains are dead rn (Fatal Error)"

async def get_combined_response(user_id, text_input, image_input=None, prompt_override=None):
    global cooldowns, fail_counts
    
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

    # 2. Reset Expired Cooldowns
    now = datetime.datetime.now()
    for layer in cooldowns:
        if cooldowns[layer] and now > cooldowns[layer]:
            cooldowns[layer] = None
            print(f"INFO: Layer {layer} Cooldown Expired. Ready.")

    response_text = ""
    successful_layer = None
    gemini_layers = [(model_1, 1, "Gemini 3"), (model_2, 2, "Gemini 2.5"), (model_3, 3, "Gemini 2.0"), (model_4, 4, "Gemini 1.5")]

    # 4. Iterate Layers
    for model, layer_num, name in gemini_layers:
        if successful_layer: break
        if not cooldowns[layer_num]:
            try:
                gemini_history = history_db + [{"role": "user", "parts": [current_text]}]
                if image_input: gemini_history[-1]["parts"].append(image_input)
                response = await model.generate_content_async(gemini_history)
                response_text = response.text
                successful_layer = name
                fail_counts[layer_num] = 0
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "ResourceExhausted" in error_str or "503" in error_str or "not found" in error_str:
                    fail_counts[layer_num] += 1
                    wait_time = datetime.timedelta(hours=24) if fail_counts[layer_num] >= 2 else datetime.timedelta(minutes=1)
                    cooldowns[layer_num] = now + wait_time
                    print(f"⚠️ {name} FAILED. Switching to next layer.")
                else:
                    cooldowns[layer_num] = now + datetime.timedelta(seconds=10)

    # 5. Fallback to Groq
    if not successful_layer:
        if image_input and not text_input: return "cant see images rn just text me"
        response_text = await call_groq_fallback(history_db, SYSTEM_PROMPT, current_text)

    # 6. Save
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
        try:
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
        except discord.Forbidden: print(f"Perms Missing in {message.channel.name}.")
        except Exception: pass

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')

# --- SYNC COMMAND (Keep as prefix to register slash commands) ---
@bot.command()
@commands.is_owner()
async def sync(ctx, guilds: commands.Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
    if not guilds:
        if spec == "~": synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "*":
            ctx.bot.tree.copy_global_to(guild=ctx.guild)
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "^":
            ctx.bot.tree.clear_commands(guild=ctx.guild)
            await ctx.bot.tree.sync(guild=ctx.guild)
            synced = []
        else: synced = await ctx.bot.tree.sync()
        await ctx.send(f"Synced {len(synced)} commands.")
        return
    ret = 0
    for guild in guilds:
        try: await ctx.bot.tree.sync(guild=guild)
        except discord.HTTPException: pass
        else: ret += 1
    await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

# --- CONVERTED SLASH COMMANDS ---

@bot.tree.command(name="help", description="See what Yuri can do.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="✨ Yuri's Chaos Menu", description="Chat, Roast, Ship, etc.", color=discord.Color.from_rgb(255, 105, 180))
    embed.add_field(name="Commands", value="/roast, /ship, /rate, /truth, /dare, /rename", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rename", description="Give someone a chaotic nickname.")
@app_commands.describe(member="The user to rename")
async def rename(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer() # Wait for AI
    if interaction.guild.me.top_role <= member.top_role:
        await interaction.followup.send("Can't rename them, they are too strong lol.")
        return
    
    prompt = f"Create a funny, slightly mean nickname for {member.display_name}. Max 2 words."
    raw_response = await get_combined_response(interaction.user.id, None, prompt_override=prompt)
    new_nick = raw_response.replace('"', '').strip()[:32]
    
    try: 
        await member.edit(nick=new_nick)
        await interaction.followup.send(f"Lol ok you are now **{new_nick}** ✨")
    except: 
        await interaction.followup.send(f"I chose **{new_nick}**, but Discord blocked me from changing it. 🙄")

@bot.tree.command(name="roast", description="Absolutely destroy someone.")
@app_commands.describe(member="The victim")
async def roast(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    response = await get_combined_response(interaction.user.id, None, prompt_override=f"Roast {member.display_name} hard.")
    await interaction.followup.send(f"{member.mention} {response}")

@bot.tree.command(name="rate", description="Rate someone's vibe (0-100%).")
@app_commands.describe(member="The person to rate")
async def rate(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    response = await get_combined_response(interaction.user.id, None, prompt_override=f"Rate {member.display_name}'s vibe from 0 to 100%. Be mean and sarcastic.")
    await interaction.followup.send(f"{member.mention} {response}")

@bot.tree.command(name="ship", description="Check compatibility between two people.")
@app_commands.describe(member1="First person", member2="Second person (optional)")
async def ship(interaction: discord.Interaction, member1: discord.Member, member2: Optional[discord.Member] = None):
    await interaction.response.defer()
    target2 = member2 if member2 else interaction.user
    prompt = f"Ship {member1.display_name} and {target2.display_name}. Give a % and a funny prediction."
    response = await get_combined_response(interaction.user.id, None, prompt_override=prompt)
    await interaction.followup.send(response)

@bot.tree.command(name="ask", description="Ask Yuri a Yes/No question.")
@app_commands.describe(question="Your question")
async def ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer()
    prompt = f"Answer this yes/no question sassily: {question}"
    response = await get_combined_response(interaction.user.id, None, prompt_override=prompt)
    await interaction.followup.send(f"**Q:** {question}\n**A:** {response}")

@bot.tree.command(name="truth", description="Get a spicy Truth question.")
async def truth(interaction: discord.Interaction):
    await interaction.response.defer()
    response = await get_combined_response(interaction.user.id, None, prompt_override="Give a funny, spicy teenage Truth question.")
    await interaction.followup.send(f"**TRUTH:** {response}")

@bot.tree.command(name="dare", description="Get a chaotic Dare.")
async def dare(interaction: discord.Interaction):
    await interaction.response.defer()
    response = await get_combined_response(interaction.user.id, None, prompt_override="Give a funny, chaotic Dare for a discord user.")
    await interaction.followup.send(f"**DARE:** {response}")

@bot.tree.command(name="wipe", description="Admin Only: Wipe memory.")
@app_commands.describe(member="User to wipe (leave empty for ALL)")
async def wipe(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    if str(interaction.user.id) != str(OWNER_ID):
        await interaction.response.send_message("Nice try, you're not my owner. 🙄", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    if member:
        await clear_user_history(member.id)
        await interaction.followup.send(f"Wiped memories for {member.display_name}.")
    else:
        await clear_all_history()
        await interaction.followup.send("Wiped ALL database memories.")

bot.run(os.getenv('DISCORD_TOKEN'))

