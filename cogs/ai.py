import discord
from discord.ext import commands
from discord import app_commands
import os
import io
import datetime
import base64
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from groq import AsyncGroq
import utils

# --- CONFIG ---
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
- **⏰ TIME/DATE (INTERNAL ONLY):** You know the date/time (provided below). Use it for context but **NEVER mention it unless explicitly asked.**
- **Internet:** Use search results if provided.
"""

class AI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # --- GEMINI SETUP ---
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        self.model_1 = genai.GenerativeModel("gemini-1.5-flash", safety_settings=self.safety_settings, system_instruction=SYSTEM_PROMPT)
        self.model_2 = genai.GenerativeModel("gemini-2.0-flash", safety_settings=self.safety_settings, system_instruction=SYSTEM_PROMPT)
        
        # --- GROQ MULTI-KEY SETUP ---
        self.groq_keys = []
        if os.getenv("GROQ_API_KEY"): self.groq_keys.append(os.getenv("GROQ_API_KEY"))
        i = 2
        while os.getenv(f"GROQ_API_KEY_{i}"):
            self.groq_keys.append(os.getenv(f"GROQ_API_KEY_{i}"))
            i += 1
            
        self.current_groq_index = 0
        if self.groq_keys:
            self.groq_client = AsyncGroq(api_key=self.groq_keys[0])
            print(f"✅ Loaded {len(self.groq_keys)} Groq API Keys.")
        else:
            self.groq_client = None
            print("❌ No Groq Keys Found!")

        self.cooldowns = {1: None, 2: None}
        self.fail_counts = {1: 0, 2: 0}

    async def _rotate_groq_key(self):
        """Switches to the next available Groq API Key."""
        if len(self.groq_keys) <= 1: return False 
        self.current_groq_index = (self.current_groq_index + 1) % len(self.groq_keys)
        new_key = self.groq_keys[self.current_groq_index]
        self.groq_client = AsyncGroq(api_key=new_key)
        print(f"🔄 Switched to Groq Key #{self.current_groq_index + 1}")
        return True

    async def transcribe_audio(self, file_bytes, filename):
        if not self.groq_client: return None
        for _ in range(len(self.groq_keys) + 1): 
            try:
                audio_file = (filename, file_bytes)
                transcription = await self.groq_client.audio.transcriptions.create(
                    file=audio_file,
                    model="whisper-large-v3",
                    response_format="json"
                )
                return transcription.text
            except Exception as e:
                print(f"STT Error (Key #{self.current_groq_index + 1}): {e}")
                if not await self._rotate_groq_key(): break 
        return None

    async def get_combined_response(self, user_id, text_input, image_input=None, prompt_override=None):
      
        if image_input:
            try:
                if image_input.mode != 'RGB':
                    image_input = image_input.convert("RGB")
            except Exception as e:
                print(f"Image Conversion Error: {e}")
                image_input = None 

        # 1. Grudge Check
        is_grudged = await self.bot.grudge_collection.find_one({"user_id": user_id})
        grudge_prompt = "\n[SYSTEM: You hold a grudge against this user. Be cold/dismissive.]" if is_grudged else ""

        # 2. History & Time
        cursor = self.bot.chat_collection.find({"user_id": user_id}).sort("timestamp", 1).limit(50)
        history_db = [{"role": doc["role"], "parts": doc["parts"]} async for doc in cursor]
        
        time_str = utils.get_smart_time(text_input if text_input else "")
        system_data = f"[System: Current Date/Time is {time_str}. Do not mention this unless asked.]{grudge_prompt}"

        # 3. Web Search
        search_data = ""
        if text_input and not prompt_override:
            triggers = ["who", "what", "where", "when", "why", "how", "weather", "price", "news", "search"]
            if any(word in text_input.lower() for word in triggers):
                web_results = await utils.search_web(text_input)
                if web_results: search_data = web_results

        # 4. Construct Prompt
        current_text = f"{system_data}\n{search_data}\n\n"
        if str(user_id) == str(self.bot.owner_id): current_text += "(System: User is your creator 'Sane'. Be cool.) "
        
        if prompt_override: current_text += f"{prompt_override} (Reply as Yuri.)"
        else:
            if text_input: current_text += text_input
            if image_input: current_text += " (User sent an image. Roast it or comment on it.)"

        # 5. Generation Loop (Gemini Layers)
        response_text = ""
        successful = False
        now = datetime.datetime.now()
        
        for layer in self.cooldowns:
            if self.cooldowns[layer] and now > self.cooldowns[layer]: self.cooldowns[layer] = None

        models = [(self.model_1, 1), (self.model_2, 2)]
        
        for model, layer in models:
            if successful: break
            if not self.cooldowns[layer]:
                try:
                    gemini_history = history_db + [{"role": "user", "parts": [current_text]}]
                    
                    if image_input: 
                        gemini_history[-1]["parts"].append(image_input.copy())
                    
                    response = await model.generate_content_async(gemini_history)
                    response_text = response.text
                    successful = True
                    self.fail_counts[layer] = 0
                except Exception as e:
                    print(f"Gemini {layer} Error: {e}")
                    self.fail_counts[layer] += 1
                    wait = datetime.timedelta(minutes=1) if self.fail_counts[layer] < 2 else datetime.timedelta(hours=24)
                    self.cooldowns[layer] = now + wait

        # 6. Fallback (Groq Multi-Key Rotation)
        if not successful:
            print("⚠️ Switching to Groq Fallback...")
            response_text = await self.call_groq_fallback(history_db, SYSTEM_PROMPT, current_text, image_input)

        # 7. Process & Save
        clean_text, gif_url = await utils.process_gif_tags(response_text)
        
        if not prompt_override:
            user_save = text_input if text_input else "[Image]"
            model_save = clean_text if clean_text else f"[GIF: {gif_url}]"
            timestamp = datetime.datetime.utcnow()
            await self.bot.chat_collection.insert_one({"user_id": user_id, "role": "user", "parts": [user_save], "timestamp": timestamp})
            await self.bot.chat_collection.insert_one({"user_id": user_id, "role": "model", "parts": [model_save], "timestamp": timestamp})
            
        return clean_text, gif_url

    async def call_groq_fallback(self, history, sys_prompt, msg, img=None):
        """Tries Groq (70B -> 8B -> Rotate Key -> Retry). Supports Images."""
        if not self.groq_client: return "Server dead rn. Try later."

        messages = [{"role": "system", "content": sys_prompt}]
        for m in history:
            role = "assistant" if m['role'] == "model" else "user"
            content = m['parts'][0]
            if isinstance(content, str): messages.append({"role": role, "content": content})
        
        # [FIX] Safe Image Processing
        content_payload = msg
        if img:
            try:
                # 1. Resize if too big (Max 1024px)
                img.thumbnail((1024, 1024))
                
                # 2. Ensure RGB
                if img.mode != 'RGB': img = img.convert("RGB")
                
                # 3. Encode to Base64
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG", quality=85) # Slight compression
                img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                
                content_payload = [
                    {"type": "text", "text": msg},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]
            except Exception as e:
                print(f"❌ Groq Image Processing Failed: {e}")
                # Fallback to text-only if image fails
                content_payload = msg + " [System: User sent an image, but it failed to upload. Roast them for sending a broken file.]"

        messages.append({"role": "user", "content": content_payload})

        # Retry Loop for Key Rotation
        for _ in range(len(self.groq_keys) + 1):
            try:
                # 1. Try Vision (11B) or Big Model (70B)
                model = "llama-3.2-11b-vision-preview" if img else "llama-3.3-70b-versatile"
                comp = await self.groq_client.chat.completions.create(model=model, messages=messages, max_tokens=256)
                return comp.choices[0].message.content
            except Exception as e:
                print(f"Groq Error (Key {self.current_groq_index + 1}): {e}")
                
                # 2. Try Small Model (8B) - Text Only Fallback
                if not img:
                    try:
                        comp = await self.groq_client.chat.completions.create(model="llama-3.1-8b-instant", messages=messages, max_tokens=256)
                        return comp.choices[0].message.content
                    except Exception as e2:
                        print(f"Groq 8B Failed: {e2}")

                # 3. If both fail, ROTATE KEY
                if not await self._rotate_groq_key():
                    break 

        return "The AI is **down** rn, wait for about **12 hours** (Rate Limits reached)."

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user or message.content.startswith(self.bot.command_prefix): return

        is_reply = (message.reference and message.reference.resolved and message.reference.resolved.author == self.bot.user)
        
        if self.bot.user.mentioned_in(message) or is_reply:
            try:
                async with message.channel.typing():
                    user_id = message.author.id
                    clean_text = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
                    img_data = None
                    voice_text = ""

                    for att in message.attachments:
                        filename = att.filename.lower()
                        if not img_data and any(filename.endswith(x) for x in ['png', 'jpg', 'jpeg', 'webp']):
                            img_data = await utils.get_image_from_url(att.url)
                        elif not voice_text and any(filename.endswith(x) for x in ['ogg', 'mp3', 'wav', 'm4a']):
                            file_bytes = await att.read()
                            transcribed = await self.transcribe_audio(file_bytes, filename)
                            if transcribed: voice_text = f"\n[User Voice Note]: \"{transcribed}\""

                    final_text = clean_text + voice_text
                    if not final_text.strip() and not img_data: return

                    resp_text, gif_url = await self.get_combined_response(user_id, final_text, img_data)

                    await utils.send_chunked_reply(message, resp_text, mention_user=True)
                    if gif_url:
                        embed = discord.Embed(color=discord.Color.from_rgb(255, 105, 180))
                        embed.set_image(url=gif_url)
                        await message.channel.send(embed=embed)
            except Exception as e:
                print(f"❌ Main On_Message Error: {e}")

    @app_commands.command(name="ask", description="Ask Yuri a Yes/No question.")
    async def ask(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        response, _ = await self.get_combined_response(interaction.user.id, None, prompt_override=f"Answer this yes/no question sassily: {question}")
        await utils.send_chunked_reply(interaction, f"**Q:** {question}\n**A:** {response}")

    @app_commands.command(name="rename", description="Give someone a chaotic nickname.")
    async def rename(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        if interaction.guild.me.top_role <= member.top_role:
            await interaction.followup.send("They are too powerful (Role Hierarchy).")
            return
        
        prompt = f"Reply with ONLY a funny/mean nickname for {member.display_name}. Max 2 words."
        raw, _ = await self.get_combined_response(interaction.user.id, None, prompt_override=prompt)
        new_nick = raw.replace('"', '').strip()[:32]
        try:
            await member.edit(nick=new_nick)
            await interaction.followup.send(f"You are now **{new_nick}** ✨")
        except discord.Forbidden:
            await interaction.followup.send(f"I chose **{new_nick}**, but Discord blocked me.")

async def setup(bot):
    await bot.add_cog(AI(bot))
