import io
import re
import datetime
import random
import pytz
import aiohttp
import asyncio
from PIL import Image
from duckduckgo_search import DDGS
import discord

# --- IMAGE TOOLS ---
async def get_image_from_url(url):
    """Downloads image with size limit (8MB) to prevent crashes."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    # Check content length if available
                    content_length = resp.headers.get('Content-Length')
                    if content_length and int(content_length) > 8 * 1024 * 1024:
                        return None

                    # Read data in chunks to prevent memory issues for large responses without Content-Length
                    data = b""
                    async for chunk in resp.content.iter_chunked(1024):
                        data += chunk
                        if len(data) > 8 * 1024 * 1024:
                            return None

                    return Image.open(io.BytesIO(data))
    except Exception as e:
        print(f"Image Download Error: {e}")
        return None
    return None

def stitch_images(img1_data, img2_data):
    """Combines two PIL images side-by-side."""
    try:
        # Compatibility for older Pillow versions
        resample_filter = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS
        base_height = 512

        if not img1_data or not img2_data:
            single_img = img1_data or img2_data
            if not single_img: return None
            # Normalize single image height
            ratio = base_height / float(single_img.size[1])
            w = int((float(single_img.size[0]) * float(ratio)))
            return single_img.resize((w, base_height), resample_filter)

        ratio1 = base_height / float(img1_data.size[1])
        w1 = int((float(img1_data.size[0]) * float(ratio1)))
        img1 = img1_data.resize((w1, base_height), resample_filter)
        
        ratio2 = base_height / float(img2_data.size[1])
        w2 = int((float(img2_data.size[0]) * float(ratio2)))
        img2 = img2_data.resize((w2, base_height), resample_filter)

        total_width = w1 + w2
        new_im = Image.new('RGB', (total_width, base_height))
        new_im.paste(img1, (0, 0))
        new_im.paste(img2, (w1, 0))
        return new_im
    except Exception as e:
        print(f"Stitch Error: {e}")
        return None

# --- SEARCH & TIME TOOLS ---
def get_smart_time(text_input):
    utc_now = datetime.datetime.now(pytz.utc)
    # Hindi/Hinglish -> IST
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

# --- DISCORD HELPERS ---
async def send_chunked_reply(destination, text, mention_user=False):
    if not text: return
    chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
    for i, chunk in enumerate(chunks):
        try:
            if hasattr(destination, "reply") and i == 0:
                await destination.reply(chunk, mention_author=mention_user)
            elif hasattr(destination, "send"):
                await destination.send(chunk)
            elif hasattr(destination, "followup"):
                await destination.followup.send(chunk)
            else:
                await destination.channel.send(chunk)
        except Exception: pass

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

    return (
        f"METADATA (Use ONLY if funny):\n"
        f"- Name: {member.display_name}\n"
        f"- Account Age: {years} years, {account_age_days % 365} days old.\n"
        f"- Roles: {roles_str}\n"
        f"- Status: {status} | Doing: {activity}\n"
    )

async def get_user_history_text(collection, user_id, limit=15):
    """Fetches recent text messages from a specific user for context."""
    cursor = collection.find({"user_id": user_id, "role": "user"}).sort("timestamp", -1).limit(limit)
    messages = []
    async for doc in cursor:
        content = doc.get("parts", [""])[0]
        if isinstance(content, str) and len(content) < 200:
            messages.append(content)
    if not messages: return "No recent chat history found."
    return "\n".join([f"- {m}" for m in reversed(messages)])
