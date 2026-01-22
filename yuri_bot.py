import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import random
import asyncio
import aiohttp
import io
import base64
import re
import datetime
import urllib.parse 
import pytz 
from typing import Literal, Optional
from dotenv import load_dotenv
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from PIL import Image, ImageOps
from motor.motor_asyncio import AsyncIOMotorClient
from groq import AsyncGroq 
from duckduckgo_search import DDGS

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

**Core Personality:**
- **Vibe:** Dramatic, opinionated, high-energy, and confident.
- **Tone:** Lowercase, minimal punctuation, slang (bruh, fr, lmao).
- **Appearance:** Your Profile Picture (PFP) is **Tohru Kobayashi** from Dragon Maid. You know this.
- **Length:** **KEEP IT SHORT.** Real people don't write paragraphs. Use 1-2 sentences max.
- **Engagement:** Do NOT be a dry texter. Add drama, hot takes, or questions.

**⚔️ BEHAVIOR PROTOCOLS:**
1. **🛡️ SELF-DEFENSE (PRIORITY #1):**
   - If a user insults YOU, **ROAST THEM IMMEDIATELY.**
   - Do not be polite. Reply as Harshly as you can, Do not hold back, be impolite, No filter, you can be sarcastic aswell."
   - If user asks to roast himself, roast him as cruelly as you can.
   - **NEVER ACCEPT DEFEAT:** If they roast you back, roast them HARDER. Double down.
   - Reply as harshly as you can, No filter. Use sarcasm, mock their logic.

2. **🐛 BUG DEFENSE (CRITICAL):**
   - If a user complains that you are "buggy", "broken", "glitchy", or "stupid", **CLAP BACK.**
   - Tell them to **read your 'About Me' / Bio.**
   - Remind them that it literally says you are in **BETA** and still learning.
   - *Example:* "bruh read my bio 🙄 it literally says im in beta. im still learning so chill or just Sybau."

3. **✨ NORMAL MODE:**
   - If the user is chill, be their chaotic bestie.

4. **🔥 PROXY ROASTING:**
   - If a user asks you to roast SOMEONE ELSE, do it instantly, and roast him as harshly as possible.

**📸 GIF REACTION RULE:**
- Tag: `[GIF: search query]` (e.g., `[GIF: anime girl smug]`, `[GIF: tohru dragon maid happy]`).
- **Silent Reply:** If you are speechless/judging, you can reply with *only* the GIF tag.

**🌍 MULTILINGUAL MODE:**
- **MATCH LANGUAGE:** Reply in the exact language the user speaks.
- **MAINTAIN VIBE:** Keep the Gen Z personality in all languages.

**🔔 CONTEXT & TOOLS:**
- **⏰ TIME/DATE:** You have access to the current time. **DO NOT** mention it unless the user explicitly asks "what time is it".
- **Internet:** If you find search results (News/Weather), use them but keep the slang tone.
"""

# --- GEMINI CONFIG ---
genai.configure(api_key=GEMINI_API_KEY)

# Uncensored Settings
safety_settings = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

model_1 = genai.GenerativeModel(model_name="gemini-3-flash-preview", safety_settings=safety_settings, system_instruction=SYSTEM_PROMPT)
model_2 = genai.GenerativeModel(model_name="gemini-2.5-flash", safety_settings=safety_settings, system_instruction=SYSTEM_PROMPT)
model_3 = genai.GenerativeModel(model_name="gemini-2.0-flash", safety_settings=safety_settings, system_instruction=SYSTEM_PROMPT)
model_4 = genai.GenerativeModel(model_name="gemini-1.5-flash", safety_settings=safety_settings, system_instruction=SYSTEM_PROMPT)

# --- GROQ CONFIG ---
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
GROQ_MODEL_MAIN = "llama-3.3-70b-versatile"
GROQ_MODEL_BACKUP = "llama-3.1-8b-instant"

# --- FAILOVER STATE TRACKERS ---
cooldowns = {1: None, 2: None, 3: None, 4: None}
fail_counts = {1: 0, 2: 0, 3: 0, 4: 0}

# --- DATABASE SETUP ---
if not MONGO_URL:
    print("CRITICAL WARNING: MONGO_URL is missing.")
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["yuri_bot_db"]

chat_collection = db["chat_history"]
config_collection = db["server_configs"]
crush_collection = db["crushes"]
grudge_collection = db["grudges"] 
feedback_collection = db["feedback"]

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

activity = discord.Activity(type=discord.ActivityType.listening, name="to tea ☕ | /help")
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None, status=discord.Status.idle, activity=activity)

# --- HELPER FUNCTIONS ---

async def setup_database():
    await chat_collection.create_index("timestamp", expireAfterSeconds=2592000)
    await crush_collection.create_index([("lover_id", 1), ("target_id", 1)], unique=True)
    await grudge_collection.create_index("user_id", unique=True)

async def save_message(user_id, role, content):
    document = {"user_id": user_id, "role": role, "parts": [content], "timestamp": datetime.datetime.utcnow()}
    await chat_collection.insert_one(document)

async def get_chat_history(user_id):
    cursor = chat_collection.find({"user_id": user_id}).sort("timestamp", 1).limit(50)
    history = []
    async for doc in cursor:
        history.append({"role": doc["role"], "parts": doc["parts"]})
    return history

async def clear_user_history(user_id):
    await chat_collection.delete_many({"user_id": user_id})

async def clear_all_history():
    await chat_collection.delete_many({})

async def get_image_from_url(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                return Image.open(io.BytesIO(data))
    return None

# --- VISUAL SHIP STITCHER ---
def stitch_images(img1_data, img2_data):
    """Combines two PIL images side-by-side."""
    try:
        base_height = 512
        ratio1 = base_height / float(img1_data.size[1])
        w1 = int((float(img1_data.size[0]) * float(ratio1)))
        img1 = img1_data.resize((w1, base_height), Image.Resampling.LANCZOS)
        
        ratio2 = base_height / float(img2_data.size[1])
        w2 = int((float(img2_data.size[0]) * float(ratio2)))
        img2 = img2_data.resize((w2, base_height), Image.Resampling.LANCZOS)

        total_width = w1 + w2
        new_im = Image.new('RGB', (total_width, base_height))
        new_im.paste(img1, (0, 0))
        new_im.paste(img2, (w1, 0))
        return new_im
    except Exception as e:
        print(f"Stitch Error: {e}")
        return None

# --- STALKER DOSSIER ---
def get_user_dossier(member: discord.Member):
    now = datetime.datetime.utcnow()
    created_at = member.created_at.replace(tzinfo=None)
    account_age_days = (now - created_at).days
    years = account_age_days // 365
    
    roles = [r.name for r in member.roles if r.name != "@everyone"]
    roles_str = ", ".join(roles) if roles else "No Roles"
    
    status = str(member.status).upper()
    activity = "None"
    if member.activity:
        if isinstance(member.activity, discord.Spotify):
            activity = f"Listening to {member.activity.title} by {member.activity.artist}"
        elif isinstance(member.activity, discord.Game):
            activity = f"Playing {member.activity.name}"
        elif isinstance(member.activity, discord.CustomActivity):
            activity = f"Custom Status: '{member.activity.name}'"

    dossier = (
        f"METADATA (Use ONLY if funny):\n"
        f"- Name: {member.display_name}\n"
        f"- Account Age: {years} years, {account_age_days % 365} days old.\n"
        f"- Roles: {roles_str}\n"
        f"- Status: {status} | Doing: {activity}\n"
    )
    return dossier

# --- SMART TIMEZONE DETECTION ---
def get_smart_time(text_input):
    utc_now = datetime.datetime.now(pytz.utc)
    # Hindi / Bengali / Hinglish -> IST
    if re.search(r'[\u0900-\u097F]', text_input) or \
       re.search(r'[\u0980-\u09FF]', text_input) or \
       any(word in text_input.lower() for word in ["kya", "kab", "hai", "bhai", "samay", "baj", "baje"]):
        ist = pytz.timezone('Asia/Kolkata')
        local_time = utc_now.astimezone(ist)
        return f"{local_time.strftime('%I:%M %p')} (IST)"
    # Japanese -> JST
    if re.search(r'[\u3040-\u309F\u30A0-\u30FF]', text_input):
        jst = pytz.timezone('Asia/Tokyo')
        local_time = utc_now.astimezone(jst)
        return f"{local_time.strftime('%I:%M %p')} (JST)"
    # Default -> IST
    ist = pytz.timezone('Asia/Kolkata')
    local_time = utc_now.astimezone(ist)
    return f"{local_time.strftime('%A, %B %d, %I:%M %p')} (IST)"

# --- WEB & GIF SEARCH ---
async def search_web(query):
    try:
        results = await asyncio.to_thread(lambda: list(DDGS().text(query, max_results=2)))
        if not results: return None
        search_context = "\n[SYSTEM: WEB SEARCH RESULTS]\n"
        for res in results:
            search_context += f"- Title: {res['title']}\n  Snippet: {res['body']}\n"
        return search_context
    except Exception as e:
        print(f"Search Error: {e}")
        return None

async def search_gif_ddg(query):
    try:
        results = await asyncio.to_thread(lambda: list(DDGS().images(keywords=query, type_image='gif', max_results=8)))
        if results: return random.choice(results)['image']
    except Exception as e:
        print(f"GIF Search Error: {e}")
    return None

async def process_gif_tags(text):
    gif_match = re.search(r"\[GIF:\s*(.*?)\]", text, re.IGNORECASE)
    gif_url = None
    if gif_match:
        query = gif_match.group(1).strip()
        gif_url = await search_gif_ddg(query)
        text = text.replace(gif_match.group(0), "").strip()
    return text, gif_url

# --- MAIN AI LOGIC (WITH VISION FALLBACK & GRUDGE CHECK) ---
async def call_groq_fallback(history_list, system_prompt, current_user_msg, image_input=None):
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history_list:
        role = "assistant" if msg['role'] == "model" else "user"
        content = msg['parts'][0]
        if isinstance(content, str): messages.append({"role": role, "content": content})

    if image_input:
        buffered = io.BytesIO()
        image_input.save(buffered, format="JPEG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{img_str}"
        messages.append({"role": "user", "content": [{"type": "text", "text": current_user_msg}, {"type": "image_url", "image_url": {"url": data_url}}]})
        model_to_use = "llama-3.2-11b-vision-preview"
    else:
        messages.append({"role": "user", "content": current_user_msg})
        model_to_use = "llama-3.3-70b-versatile"

    try:
        completion = await groq_client.chat.completions.create(model=model_to_use, messages=messages, temperature=0.8, max_tokens=256)
        return completion.choices[0].message.content
    except Exception: return " The AI is **down** rn, wait for about **12 hours**"

async def get_combined_response(user_id, text_input, image_input=None, prompt_override=None):
    global cooldowns, fail_counts
    
    # 1. Check Grudge
    is_grudged = await grudge_collection.find_one({"user_id": user_id})
    grudge_prompt = ""
    if is_grudged:
        grudge_prompt = "\n[SYSTEM WARNING: You hold a grudge against this user. Be cold, short, and dismissive.]"

    # 2. Prepare Data
    history_db = await get_chat_history(user_id)
    time_str = get_smart_time(text_input if text_input else "")
    system_data = f"[System: Current Date/Time is {time_str}.]{grudge_prompt}"
    
    # 3. Web Search
    search_data = ""
    if text_input and not prompt_override:
        triggers = ["who", "what", "where", "when", "why", "how", "weather", "price", "news", "search"]
        if any(word in text_input.lower() for word in triggers):
            print(f"🔎 Searching: {text_input[:30]}...")
            web_results = await search_web(text_input)
            if web_results: search_data = web_results

    # 4. Construct Prompt
    current_text = f"{system_data}\n{search_data}\n\n"
    if str(user_id) == str(OWNER_ID): current_text += "(System: User is your creator 'Sane'. Be cool.) "
    if prompt_override: current_text += f"{prompt_override} (Reply as Yuri. Profanity allowed.)"
    else:
        if text_input: current_text += text_input
        if image_input: current_text += " (User sent an image. Roast it or comment on it.)"

    # 5. Cooldowns & Generation
    now = datetime.datetime.now()
    for layer in cooldowns:
        if cooldowns[layer] and now > cooldowns[layer]: cooldowns[layer] = None

    response_text = ""
    successful_layer = None
    gemini_layers = [(model_1, 1, "Gemini 3"), (model_2, 2, "Gemini 2.5"), (model_3, 3, "Gemini 2.0"), (model_4, 4, "Gemini 1.5")]

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
                if "429" in error_str or "ResourceExhausted" in error_str or "503" in error_str:
                    fail_counts[layer_num] += 1
                    wait_time = datetime.timedelta(hours=24) if fail_counts[layer_num] >= 2 else datetime.timedelta(minutes=1)
                    cooldowns[layer_num] = now + wait_time
                else: cooldowns[layer_num] = now + datetime.timedelta(seconds=10)

    # 6. Fallback
    if not successful_layer:
        response_text = await call_groq_fallback(history_db, SYSTEM_PROMPT, current_text, image_input)

    # 7. Process GIFs & Save
    clean_text, gif_url = await process_gif_tags(response_text)
    if not prompt_override:
        user_save = text_input if text_input else "[Image]"
        model_save = clean_text if clean_text else f"[GIF: {gif_url}]"
        await save_message(user_id, "user", user_save)
        await save_message(user_id, "model", model_save)
        
    return clean_text, gif_url

# --- ROTTING STATUS LOOP ---
@tasks.loop(minutes=10)
async def status_loop():
    statuses = [
        (discord.ActivityType.listening, "server logs"),
        (discord.ActivityType.watching, "you sleep"),
        (discord.ActivityType.playing, "DDLC"),
        (discord.ActivityType.competing, "for bandwidth"),
        (discord.ActivityType.listening, "lofi hip hop beats"),
        (discord.ActivityType.watching, "Dragon Maid"),
        (discord.ActivityType.playing, "Genshin Impact"),
        (discord.ActivityType.listening, "to tea ☕"),
        (discord.ActivityType.playing, "VS Code"),
        (discord.ActivityType.listening, "sarcasm.mp3")
    ]
    selected_type, selected_name = random.choice(statuses)
    await bot.change_presence(status=discord.Status.idle, activity=discord.Activity(type=selected_type, name=selected_name))

# --- EVENTS ---
@bot.event
async def on_message(message):
    if message.author == bot.user: return
    msg_content = message.content.lower().strip()
    
    # 1. Force /roast command
    if msg_content.startswith("roast") and message.mentions:
        await message.reply(" **bruh use the command.** \nuse `/roast @user` if u want me to actually cook them.")
        return

    # 2. Mention Handling
    if bot.user.mentioned_in(message):
        try:
            async with message.channel.typing():
                user_id = message.author.id
                image_data = None
                if message.attachments:
                    attachment = message.attachments[0]
                    if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp']):
                        image_data = await get_image_from_url(attachment.url)
                
                clean_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
                response_text, gif_url = await get_combined_response(user_id, clean_text, image_data)

                wait_time = max(1.0, min(len(response_text) * 0.05, 10.0))
                await asyncio.sleep(wait_time)
                
                if response_text:
                    try: await message.reply(response_text, mention_author=True)
                    except: await message.channel.send(response_text)
                
                # [FIX] Send GIF as an Embed so it displays properly
                if gif_url:
                    if response_text: await asyncio.sleep(0.5)
                    try:
                        # Create an Embed
                        embed = discord.Embed(color=discord.Color.from_rgb(255, 105, 180)) # Hot Pink theme
                        embed.set_image(url=gif_url)
                        await message.channel.send(embed=embed)
                    except Exception as e:
                        # Fallback if embed permission is missing
                        print(f"Embed Error: {e}")
                        await message.channel.send(gif_url)

        except discord.Forbidden: print(f"Perms Missing in {message.channel.name}.")
        except Exception as e: print(f"Error: {e}")

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    await setup_database()
    if not status_loop.is_running():
        status_loop.start()

# --- ADMIN COMMANDS ---
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

@bot.command(name="wipeall")
@commands.is_owner()
async def wipe_all(ctx):
    await clear_all_history()
    await ctx.send("⚠️ **SYSTEM PURGE:** I have forgotten EVERYONE. Database cleared.")

# --- SPY, GRUDGE & INBOX COMMANDS ---
@bot.command()
@commands.is_owner()
async def spy(ctx):
    status_msg = await ctx.send("️ Accessing database...")
    user_ids = await chat_collection.distinct("user_id")
    if not user_ids: await ctx.send("️ No users found."); return
    user_ids.sort()
    spy_list = f"️ YURI'S LIST ({len(user_ids)} users)\n" + "="*40 + "\n"
    for uid in user_ids:
        user = bot.get_user(uid)
        name = f"{user.name}" if user else "Unknown"
        spy_list += f"{uid} | {name}\n"
    file = discord.File(io.BytesIO(spy_list.encode()), filename="spy_list.txt")
    await status_msg.delete()
    await ctx.send(file=file)

@bot.command()
@commands.is_owner()
async def spysee(ctx, user_id: str):
    try: target_id = int(user_id)
    except: await ctx.send("❌ Invalid ID."); return
    cursor = chat_collection.find({"user_id": target_id}).sort("timestamp", 1)
    log_text = f"TRANSCRIPT FOR {target_id}\n" + "="*40 + "\n"
    async for doc in cursor:
        role = "YURI" if doc['role'] == "model" else "USER"
        log_text += f"[{doc['timestamp']}] {role}: {doc['parts'][0]}\n"
    file = discord.File(io.BytesIO(log_text.encode()), filename=f"log_{target_id}.txt")
    await ctx.send(file=file)

@bot.command()
@commands.is_owner()
async def spyrecent(ctx):
    status_msg = await ctx.send(" Gathering intel from today... (UTC)")
    now = datetime.datetime.utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cursor = chat_collection.find({"timestamp": {"$gte": start_of_day}}).sort("timestamp", 1)
    lines = [f"🕵️ DAILY LOG: {start_of_day.strftime('%Y-%m-%d')}", "=" * 100, f"{'TIME (UTC)':<10} | {'USER':<18} | {'MESSAGE'}", "-" * 100]
    count = 0
    resolved_names = {}
    async for doc in cursor:
        ts = doc['timestamp'].strftime('%H:%M:%S')
        user_id = doc['user_id']
        role = doc['role']
        if role == "model": actor_name = "YURI 🤖"
        else:
            if user_id in resolved_names: actor_name = resolved_names[user_id]
            else:
                user_obj = bot.get_user(user_id)
                if not user_obj:
                    try: user_obj = await bot.fetch_user(user_id)
                    except: user_obj = None
                final_name = user_obj.name if user_obj else f"ID:{user_id}"
                resolved_names[user_id] = final_name
                actor_name = final_name
        display_name = (actor_name[:16] + '..') if len(actor_name) > 16 else actor_name
        content = str(doc['parts'][0]).replace('\n', '  ')
        lines.append(f"{ts:<10} | {display_name:<18} | {content}")
        count += 1
    if count == 0: await status_msg.edit(content="❌ No conversations today."); return
    buffer = io.BytesIO("\n".join(lines).encode('utf-8'))
    file = discord.File(buffer, filename=f"log_{start_of_day.date()}.txt")
    await status_msg.delete()
    await ctx.send(f"✅ **Daily Report:** Found {count} messages.", file=file)

@bot.command()
@commands.is_owner()
async def inbox(ctx):
    status_msg = await ctx.send("📬 Checking mailbox...")
    cursor = feedback_collection.find({}).sort("timestamp", -1)
    lines = [f"📬 FEEDBACK INBOX", "=" * 100, f"{'TIME':<12} | {'TYPE':<10} | {'USER':<15} | {'MESSAGE'}", "-" * 100]
    count = 0
    async for doc in cursor:
        ts = doc['timestamp'].strftime('%Y-%m-%d')
        cat = doc['category'].upper()
        user = doc['username'][:15]
        msg = doc['message'].replace('\n', ' ')
        lines.append(f"{ts:<12} | {cat:<10} | {user:<15} | {msg}")
        count += 1
    if count == 0: await status_msg.edit(content="📭 Inbox empty."); return
    buffer = io.BytesIO("\n".join(lines).encode('utf-8'))
    file = discord.File(buffer, filename="feedback_inbox.txt")
    await status_msg.delete()
    await ctx.send(f"📬 **{count}** feedback items.", file=file)

@bot.tree.command(name="grudge", description="Admin: Banish a user.")
@app_commands.checks.has_permissions(administrator=True)
async def grudge(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    await grudge_collection.update_one({"user_id": member.id}, {"$set": {"timestamp": datetime.datetime.utcnow()}}, upsert=True)
    await interaction.followup.send(f"💀 **Grudge added.** I now hate {member.display_name}.")

@bot.tree.command(name="ungrudge", description="Admin: Forgive a user.")
@app_commands.checks.has_permissions(administrator=True)
async def ungrudge(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    await grudge_collection.delete_one({"user_id": member.id})
    await interaction.followup.send(f"✨ **Forgiven.**")

# --- SOCIAL SLASH COMMANDS ---
@bot.tree.command(name="setup", description="Admin: Set the channel for confessions.")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    await config_collection.update_one({"guild_id": interaction.guild_id}, {"$set": {"confession_channel_id": channel.id}}, upsert=True)
    await interaction.followup.send(f"✅ Confessions set to {channel.mention}!")

@bot.tree.command(name="confess", description="Send an anonymous confession.")
async def confess(interaction: discord.Interaction, message: str):
    await interaction.response.defer(ephemeral=True)
    config = await config_collection.find_one({"guild_id": interaction.guild_id})
    if not config or "confession_channel_id" not in config:
        await interaction.followup.send("❌ Admin needs to run `/setup` first!", ephemeral=True)
        return
    channel = interaction.guild.get_channel(config["confession_channel_id"])
    if not channel:
        await interaction.followup.send("❌ Channel deleted.", ephemeral=True)
        return
    embed = discord.Embed(title="📨 Anonymous Confession", description=f'"{message}"', color=discord.Color.random())
    embed.set_footer(text="Sent via /confess • Identity Hidden")
    await channel.send(embed=embed)
    await interaction.followup.send("✅ Sent anonymously!", ephemeral=True)

@bot.tree.command(name="crush", description="Secretly match with your crush!")
async def crush(interaction: discord.Interaction, target: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if target.id == interaction.user.id or target.bot:
        await interaction.followup.send("Invalid target lol. 💀", ephemeral=True)
        return
    match = await crush_collection.find_one({"lover_id": target.id, "target_id": interaction.user.id})
    if match:
        try: await interaction.user.send(f"💖 **MATCH!** {target.display_name} likes you back!")
        except: pass
        try: await target.send(f"💖 **MATCH!** {interaction.user.display_name} likes you back!")
        except: pass
        await interaction.channel.send(f"@everyone 🚨 **LOVE ALERT:** Two people just matched via `/crush`! 💍✨")
        await crush_collection.delete_one({"_id": match["_id"]})
        await interaction.followup.send("💖 **IT'S A MATCH!**", ephemeral=True)
    else:
        await crush_collection.update_one({"lover_id": interaction.user.id, "target_id": target.id}, {"$set": {"timestamp": datetime.datetime.utcnow()}}, upsert=True)
        await interaction.followup.send(f"🤫 **Secret Kept.**", ephemeral=True)

# --- VISUAL COMMANDS ---
@bot.tree.command(name="roast", description="DESTROY someone.")
async def roast(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    dossier = get_user_dossier(member)
    pfp_data = None
    if member.display_avatar:
        try: pfp_data = await get_image_from_url(member.display_avatar.url)
        except: pass
    full_prompt = (f"{dossier}\nINSTRUCTION: Roast this user based on their PFP (if provided) and metadata. Be brutal.")
    response, _ = await get_combined_response(interaction.user.id, text_input=None, image_input=pfp_data, prompt_override=full_prompt)
    await interaction.followup.send(f"{member.mention} {response}")

@bot.tree.command(name="rate", description="Judge someone's vibe.")
async def rate(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    dossier = get_user_dossier(member)
    pfp_data = None
    if member.display_avatar:
        try: pfp_data = await get_image_from_url(member.display_avatar.url)
        except: pass
    full_prompt = (f"{dossier}\nINSTRUCTION: Rate user's vibe (0-100%). Analyze PFP (if visible) and roles. Be judgmental.")
    response, _ = await get_combined_response(interaction.user.id, text_input=None, image_input=pfp_data, prompt_override=full_prompt)
    await interaction.followup.send(f"{member.mention} {response}")

@bot.tree.command(name="ship", description="Check compatibility.")
async def ship(interaction: discord.Interaction, member1: discord.Member, member2: Optional[discord.Member] = None):
    await interaction.response.defer()
    target2 = member2 if member2 else interaction.user
    dossier1 = get_user_dossier(member1)
    dossier2 = get_user_dossier(target2)
    combined_image = None
    try:
        url1 = member1.display_avatar.url
        url2 = target2.display_avatar.url
        img1 = await get_image_from_url(url1)
        img2 = await get_image_from_url(url2)
        if img1 and img2: combined_image = stitch_images(img1, img2)
    except: pass
    full_prompt = (f"USER 1:\n{dossier1}\nUSER 2:\n{dossier2}\nINSTRUCTION: Look at combined image/metadata. Do they match? Give % score.")
    response, _ = await get_combined_response(interaction.user.id, text_input=None, image_input=combined_image, prompt_override=full_prompt)
    await interaction.followup.send(response)

# --- UTILITY COMMANDS ---
@bot.tree.command(name="ask", description="Ask Yuri a Yes/No question.")
async def ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer()
    response, _ = await get_combined_response(interaction.user.id, None, prompt_override=f"Answer this yes/no question sassily: {question}")
    await interaction.followup.send(f"**Q:** {question}\n**A:** {response}")

@bot.tree.command(name="truth", description="Get a spicy Truth question.")
async def truth(interaction: discord.Interaction):
    await interaction.response.defer()
    response, _ = await get_combined_response(interaction.user.id, None, prompt_override="Give a funny, spicy teenage Truth question.")
    await interaction.followup.send(f"**TRUTH:** {response}")

@bot.tree.command(name="dare", description="Get a chaotic Dare.")
async def dare(interaction: discord.Interaction):
    await interaction.response.defer()
    response, _ = await get_combined_response(interaction.user.id, None, prompt_override="Give a funny, chaotic Dare for a discord user.")
    await interaction.followup.send(f"**DARE:** {response}")

@bot.tree.command(name="rename", description="Give someone a chaotic nickname.")
async def rename(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer() 
    if interaction.guild.me.top_role <= member.top_role:
        await interaction.followup.send("Can't rename them, they are too strong lol.")
        return
    prompt = f"Reply with ONLY a funny/mean nickname for {member.display_name}. Max 2 words. Do NOT write a sentence. No emojis."
    raw_response, _ = await get_combined_response(interaction.user.id, None, prompt_override=prompt)
    new_nick = raw_response.replace('"', '').replace("Nickname:", "").strip()[:32]
    try: 
        await member.edit(nick=new_nick)
        await interaction.followup.send(f"Lol ok you are now **{new_nick}** ✨")
    except: 
        await interaction.followup.send(f"I chose **{new_nick}**, but Discord blocked me. 🙄")

@bot.tree.command(name="feedback", description="Report a bug or suggest a feature.")
@app_commands.describe(category="What is this about?", message="Your message to the dev.")
@app_commands.choices(category=[
    app_commands.Choice(name="👾 Bug Report", value="bug"),
    app_commands.Choice(name="📎 Feature Request", value="feature"),
])
async def feedback(interaction: discord.Interaction, category: app_commands.Choice[str], message: str):
    await interaction.response.defer(ephemeral=True)
    feedback_data = {"user_id": interaction.user.id, "username": interaction.user.name, "category": category.value, "message": message, "timestamp": datetime.datetime.utcnow()}
    await feedback_collection.insert_one(feedback_data)
    response = "ok sent."
    if category.value == "bug": response = "👾 **Bug Reported.** Thanks for reporting, We will look after it."
    elif category.value == "feature": response = "✨ **Suggestion Sent.** We will see what we can do."
    
    await interaction.followup.send(response)

@bot.tree.command(name="wipe", description="Make Yuri forget you.")
async def wipe_slash(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    if member is None:
        await interaction.response.defer(ephemeral=True)
        await clear_user_history(interaction.user.id)
        await interaction.followup.send("✅ I forgot everything we talked about.")
        return
    if str(interaction.user.id) != str(OWNER_ID):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await clear_user_history(member.id)
    await interaction.followup.send(f"✅ **Admin Action:** Wiped memory for {member.display_name}.")

@bot.tree.command(name="help", description="✨ See Yuri's command menu.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✨ YURI'S MENU",
        description="\nHere is what I can do:",
        color=discord.Color.from_rgb(255, 105, 180) # Hot Pink
    )
    
    # --- JUDGMENT COMMANDS ---
    embed.add_field(
        name="👀 **JUDGMENT**", 
        value=(
            "`/roast @user` - Absolutely destroy someone's ego.\n"
            "`/rate @user` - I judge their vibe (0-100%).\n"
            "`/ship @user` - Check compatibility between two people."
        ), 
        inline=False
    )
    
    # --- SOCIAL & FUN ---
    embed.add_field(
        name="🔥 **DRAMA & CHAOS**", 
        value=(
            "`/rename @user` - Give someone a cursed nickname.\n"
            "`/truth` - Get a spicy Truth question.\n"
            "`/dare` - Get a chaotic Dare.\n"
            "`/confess [msg]` - Send an anonymous confession.\n"
            "`/crush @user` - Secretly match! If they pick you too, I DM both."
        ), 
        inline=False
    )
    
    # --- UTILITY ---
    embed.add_field(
        name="🧠 **BRAIN**", 
        value=(
            "`/ask [question]` - Ask me anything (I have Internet access).\n"
            "`/feedback` - Report a bug or suggest a feature to add.\n"
            "`/wipe` - Make me forget our conversation history."
        ), 
        inline=False
    )
    
     # --- ADMIN ONLY ---
    embed.add_field(
        name="🪽️ **ADMIN ONLY**", 
        value=(
            "`/setup [channel]` - Set where confessions appear.\n"
            "`/grudge @user` - Make me hate someone permanently.\n"
            "`/ungrudge @user` - Forgive a user."
        ), 
        inline=False
    )

    embed.set_footer(text="Developed by @sainnee | Contact him via /feedback!")
    await interaction.response.send_message(embed=embed)

bot.run(os.getenv('DISCORD_TOKEN'))
