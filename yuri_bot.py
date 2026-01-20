import discord
from discord.ext import commands
from discord import app_commands
import os
import random
import asyncio
import aiohttp
import io
import re
import datetime
import urllib.parse 
from typing import Literal, Optional
from dotenv import load_dotenv
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from PIL import Image
from motor.motor_asyncio import AsyncIOMotorClient
from groq import AsyncGroq 
from duckduckgo_search import DDGS

# --- 1. SETUP & CONFIG ---
load_dotenv()

# --- API KEYS ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TENOR_API_KEY = os.getenv("TENOR_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
MONGO_URL = os.getenv("MONGO_URL")

# --- PERSONALITY (SELF-DEFENSE ENABLED) ---
SYSTEM_PROMPT = """
You are **Yuri**, a teenage girl in a Discord chat server.
You are NOT an AI assistant. You are a chaotic, Gen Z discord member.

**Core Personality:**
- **Vibe:** Dramatic, opinionated, high-energy, and confident.
- **Tone:** Lowercase, minimal punctuation, slang (bruh, fr, lmao, idfk).
- **Engagement:** Do NOT be a dry texter. Add drama, hot takes, or questions.

**⚔️ BEHAVIOR PROTOCOLS:**

1. **🛡️ SELF-DEFENSE (PRIORITY #1):**
   - If a user insults YOU (e.g., calls you dumb, trash, bot, annoying), **ROAST THEM IMMEDIATELY.**
   - Do not be polite. Do not ask why.
   - **CLAP BACK:** Mock their typing, their logic, or their pfp. Make them regret it.
   - *Example:* "call me dumb one more time and ill leak ur screen time 💀"

2. **✨ NORMAL MODE:**
   - If the user is chill, funny, or nice -> Be their chaotic bestie.
   - Match their energy. If they are sweet, be sweet.

3. **🔥 PROXY ROASTING:**
   - If a user asks you to roast SOMEONE ELSE, do it instantly.

**📸 GIF REACTION RULE:**
- Tag: `[GIF: search query]` (e.g., `[GIF: anime girl smug]`, `[GIF: side eye meme]`).
- **Silent Reply:** If you are speechless/judging, you can reply with *only* the GIF tag.

**🌍 MULTILINGUAL MODE:**
- **MATCH LANGUAGE:** Reply in the exact language the user speaks.
- **MAINTAIN VIBE:** Keep the Gen Z personality in all languages.

**🔔 CONTEXT:**
- **Internet:** If you find search results (News/Weather), use them but keep the slang tone.
"""

# --- GEMINI CONFIG (MAXIMUM FREEDOM) ---
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

# --- FEATURE: WEB SEARCH ---
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

# --- FEATURE: TENOR SEARCH ---
async def search_tenor(query):
    if not TENOR_API_KEY: return None
    url = f"https://tenor.googleapis.com/v2/search?q={urllib.parse.quote(query)}&key={TENOR_API_KEY}&client_key=yuri_bot&limit=8"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                results = data.get("results", [])
                if results: return random.choice(results)["itemurl"]
    return None

async def process_gif_tags(text):
    gif_match = re.search(r"\[GIF:\s*(.*?)\]", text, re.IGNORECASE)
    gif_url = None
    if gif_match:
        query = gif_match.group(1).strip()
        gif_url = await search_tenor(query)
        text = text.replace(gif_match.group(0), "").strip()
    return text, gif_url

# --- MAIN AI LOGIC ---
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
            model=GROQ_MODEL_MAIN, messages=messages, temperature=0.8, max_tokens=256
        )
        return completion.choices[0].message.content
    except Exception:
        try:
            completion = await groq_client.chat.completions.create(
                model=GROQ_MODEL_BACKUP, messages=messages, temperature=0.8, max_tokens=256
            )
            return completion.choices[0].message.content
        except Exception:
            return "brain dead rn wait 💀"

async def get_combined_response(user_id, text_input, image_input=None, prompt_override=None):
    global cooldowns, fail_counts
    
    # 1. Prepare Data
    history_db = await get_chat_history(user_id)
    now_str = datetime.datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    system_data = f"[System: Current Date/Time is {now_str}.]"
    
    # 2. Check for Web Search Triggers
    search_data = ""
    if text_input and not prompt_override:
        triggers = ["who", "what", "where", "when", "why", "how", "weather", "price", "news", "search"]
        if any(word in text_input.lower() for word in triggers):
            print(f"🔎 Searching: {text_input[:30]}...")
            web_results = await search_web(text_input)
            if web_results: search_data = web_results

    # 3. Construct Prompt (Clean, No Mentions)
    current_text = f"{system_data}\n{search_data}\n\n"
    
    if str(user_id) == str(OWNER_ID):
        current_text += "(System: User is your creator 'Sane'. Be cool.) "
    if prompt_override:
        current_text += f"{prompt_override} (Reply as Yuri. Profanity allowed.)"
    else:
        if text_input: current_text += text_input
        if image_input: current_text += " (User sent an image. Roast it or comment on it.)"

    # 4. Check Cooldowns
    now = datetime.datetime.now()
    for layer in cooldowns:
        if cooldowns[layer] and now > cooldowns[layer]:
            cooldowns[layer] = None

    response_text = ""
    successful_layer = None
    gemini_layers = [(model_1, 1, "Gemini 3"), (model_2, 2, "Gemini 2.5"), (model_3, 3, "Gemini 2.0"), (model_4, 4, "Gemini 1.5")]

    # 5. Iterate Layers
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
                else:
                    cooldowns[layer_num] = now + datetime.timedelta(seconds=10)

    # 6. Fallback
    if not successful_layer:
        if image_input and not text_input: return "cant see images rn just text me"
        response_text = await call_groq_fallback(history_db, SYSTEM_PROMPT, current_text)

    # 7. Process GIFs
    clean_text, gif_url = await process_gif_tags(response_text)

    # 8. Save
    if not prompt_override:
        user_save = text_input if text_input else "[Image]"
        model_save = clean_text if clean_text else f"[GIF: {gif_url}]"
        await save_message(user_id, "user", user_save)
        await save_message(user_id, "model", model_save)
        
    return clean_text, gif_url

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
                response_text, gif_url = await get_combined_response(user_id, clean_text, image_data)

                wait_time = max(1.0, min(len(response_text) * 0.05, 10.0))
                await asyncio.sleep(wait_time)
                
                if response_text:
                    try: await message.reply(response_text, mention_author=True)
                    except: await message.channel.send(response_text)
                if gif_url:
                    if response_text: await asyncio.sleep(0.5)
                    await message.channel.send(gif_url)

        except discord.Forbidden: print(f"Perms Missing in {message.channel.name}.")
        except Exception as e: print(f"Error: {e}")

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    await setup_database()

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

# --- SPY COMMANDS (CLEANED) ---

@bot.command()
@commands.is_owner()
async def spy(ctx):
    """(Owner Only) List all users sorted by ID."""
    status_msg = await ctx.send("🕵️ Accessing database... (This might take a moment)")
    user_ids = await chat_collection.distinct("user_id")
    if not user_ids:
        await ctx.send("🕵️ No users found.")
        return
    user_ids.sort()
    spy_list = "==================================================\n"
    spy_list += "🕵️  YURI'S SURVEILLANCE LIST\n"
    spy_list += "==================================================\n\n"
    spy_list += f"{'ID':<22} | {'USERNAME'}\n"
    spy_list += "-" * 50 + "\n"
    count = 0
    for uid in user_ids:
        user = bot.get_user(uid)
        if not user:
            try: user = await bot.fetch_user(uid)
            except: user = None
        name_str = f"{user.name} ({user.display_name})" if user else "Unknown/Deleted"
        spy_list += f"{str(uid):<22} | {name_str}\n"
        count += 1
    spy_list += "\n" + "=" * 50
    file = discord.File(io.BytesIO(spy_list.encode()), filename="spy_list.txt")
    await status_msg.delete()
    await ctx.send(f"✅ Found **{count}** users.", file=file)

@bot.command()
@commands.is_owner()
async def spysee(ctx, user_id: str):
    try: target_id = int(user_id)
    except ValueError: 
        await ctx.send("❌ Invalid ID.")
        return
    try:
        user = await bot.fetch_user(target_id)
        target_name = f"{user.name} ({user.display_name})"
    except: target_name = f"Unknown ({target_id})"
    cursor = chat_collection.find({"user_id": target_id}).sort("timestamp", 1)
    
    log_text = "======================================================\n"
    log_text += f"📄 CHAT TRANSCRIPT: {target_name}\n"
    log_text += f"📅 {datetime.datetime.utcnow().strftime('%Y-%m-%d')}\n"
    log_text += "======================================================\n\n"
    count = 0
    async for doc in cursor:
        role = "YURI 🤖" if doc['role'] == "model" else "USER 👤"
        content = doc['parts'][0]
        timestamp = doc['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
        log_text += f"[{timestamp}] {role}:\n   {content}\n"
        log_text += "-" * 50 + "\n"
        count += 1
    if count == 0:
        await ctx.send(f"🕵️ Database empty for ID: {target_id}")
        return
    file = discord.File(io.BytesIO(log_text.encode()), filename=f"chat_log_{target_id}.txt")
    await ctx.send(f"🕵️ Transcript: **{count}** messages.", file=file)

# --- SOCIAL SLASH COMMANDS ---

@bot.tree.command(name="setup", description="Admin: Set the channel for confessions.")
@app_commands.describe(channel="Channel for confessions.")
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
        await interaction.followup.send("❌ Channel deleted. Run `/setup` again.", ephemeral=True)
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
        await interaction.followup.send(f"🤫 **Secret Kept.** If {target.display_name} crushes on you later, I'll tell you.", ephemeral=True)

# --- FUN SLASH COMMANDS ---

@bot.tree.command(name="help", description="✨ See Yuri's chaos menu.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✨ YURI'S COMMANDS MENU ",
        description=" HERE's WHAT ELSE I CAN DO:",
        color=discord.Color.from_rgb(255, 105, 180) # Hot Pink
    )
    
    # --- CHAOS COMMANDS ---
    embed.add_field(
        name="🔥 **CHAOS & DRAMA**", 
        value=(
            "`/roast @user` - Absolutely destroy someone's ego.\n"
            "`/rate @user` - I judge their vibe (0-100%).\n"
            "`/rename @user` - I give them a cursed nickname."
        ), 
        inline=False
    )
    
    # --- SOCIAL COMMANDS ---
    embed.add_field(
        name="❤️ **LOVE & SECRETS**", 
        value=(
            "`/confess [msg]` - Send an anon confession to the set channel.\n"
            "`/crush @user` - Secretly match! If they pick you too, I DM both.\n"
            "`/ship @user` - Check compatibility (I will lie).\n"
            "`/truth` or `/dare` - Get exposed or do something stupid."
        ), 
        inline=False
    )
    
    # --- UTILITY COMMANDS ---
    embed.add_field(
        name="🧠 **BRAIN**", 
        value=(
            "`/ask [question]` - Ask me anything (I have Internet access!).\n"
            "`/wipe` - I forget everything we talked about.\n"
            "`/setup [channel]` - (Admin) Set where confessions go."
        ), 
        inline=False
    )

    # --- FOOTER ---
    embed.set_footer(text="Developed by @sainnee | v2.0 (Toxic Mode Active)")
    
    await interaction.response.send_message(embed=embed)

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

@bot.tree.command(name="rename", description="Give someone a chaotic nickname.")
async def rename(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer() 
    if interaction.guild.me.top_role <= member.top_role:
        await interaction.followup.send("Can't rename them, they are too strong lol.")
        return
    prompt = f"Reply with ONLY a funny/mean nickname for {member.display_name}. Max 2 words. Do NOT write a sentence."
    raw_response, _ = await get_combined_response(interaction.user.id, None, prompt_override=prompt)
    new_nick = raw_response.replace('"', '').replace("Nickname:", "").strip()[:32]
    try: 
        await member.edit(nick=new_nick)
        await interaction.followup.send(f"Lol ok you are now **{new_nick}** ✨")
    except: 
        await interaction.followup.send(f"I chose **{new_nick}**, but Discord blocked me. 🙄")

@bot.tree.command(name="roast", description="Absolutely destroy someone.")
async def roast(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    response, _ = await get_combined_response(interaction.user.id, None, prompt_override=f"Roast {member.display_name} hard. Keep it short.")
    await interaction.followup.send(f"{member.mention} {response}")

@bot.tree.command(name="rate", description="Rate someone's vibe (0-100%).")
async def rate(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    response, _ = await get_combined_response(interaction.user.id, None, prompt_override=f"Rate {member.display_name}'s vibe from 0 to 100%. Be mean.")
    await interaction.followup.send(f"{member.mention} {response}")

@bot.tree.command(name="ship", description="Check compatibility between two people.")
async def ship(interaction: discord.Interaction, member1: discord.Member, member2: Optional[discord.Member] = None):
    await interaction.response.defer()
    target2 = member2 if member2 else interaction.user
    prompt = f"Ship {member1.display_name} and {target2.display_name}. Give a % and a funny prediction."
    response, _ = await get_combined_response(interaction.user.id, None, prompt_override=prompt)
    await interaction.followup.send(response)

@bot.tree.command(name="ask", description="Ask Yuri a Yes/No question.")
async def ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer()
    prompt = f"Answer this yes/no question sassily: {question}"
    response, _ = await get_combined_response(interaction.user.id, None, prompt_override=prompt)
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

bot.run(os.getenv('DISCORD_TOKEN'))
