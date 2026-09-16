import discord
from discord.ext import commands
from discord import app_commands

import os
import io
import re
import logging
import asyncio
import datetime
import base64
from typing import Optional

from google import genai
from google.genai import types
from groq import AsyncGroq

try:
    from together import AsyncTogether
    _TOGETHER_AVAILABLE = True
except ImportError:
    AsyncTogether = None      
    _TOGETHER_AVAILABLE = False

import utils

from cogs.prompts import SYSTEM_PROMPT

log = logging.getLogger(__name__)

USER_COOLDOWN_SECS   = 3     # minimum seconds between responses to the same user
GUILD_COOLDOWN_SECS  = 1     # minimum seconds between responses in the same server
MAX_INPUT_CHARS      = 2000  # hard cap on incoming message length
MAX_HISTORY_MESSAGES = 40    # conversation turns loaded from MongoDB per request
MAX_GROQ_TOKENS      = 256   # max tokens for all Groq completions
MIN_SEARCH_LENGTH    = 15    # messages shorter than this never trigger a web search

# Streaming response tuning
STREAM_FIRST_CHUNK_MIN_CHARS = 30    # don't send until we have at least this much
STREAM_EDIT_INTERVAL_SECS    = 1.2   # min seconds between message edit
STREAM_MAX_EDIT_INTERVAL_SECS = 0.8  # cap on how often we edit

_SEARCH_TRIGGER_RE = re.compile(
    r'\b('
    r'who is|who are|who was|who were|'
    r'what is|what are|what was|what were|what does|what did|'
    r'where is|where are|where can|'
    r'when is|when did|when does|when was|'
    r'why is|why does|why did|'
    r'how do|how does|how did|how can|how to|'
    r'weather|price of|cost of|'
    r'news|latest|current|today|right now|'
    r'search for|look up|tell me about'
    r')\b',
    re.IGNORECASE,
)


class AI(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Gemini setup (New google-genai SDK)
        self.gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.gemini_config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            safety_settings=[
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
            ]
        )

        # Gemini config with function-calling tools
        try:
            from cogs.tools import get_tool_declarations
            tool_decls = get_tool_declarations()
            self.gemini_config_with_tools = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                safety_settings=[
                    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
                    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
                ],
                tools=[types.Tool(function_declarations=tool_decls)],
            )
            log.info("Loaded %d function-calling tool(s).", len(tool_decls))
        except Exception as e:
            log.warning("Failed to load tools config, falling back to no-tools: %s", e)
            self.gemini_config_with_tools = self.gemini_config

        # Groq multi-key setup
        self.groq_keys: list[str] = []
        if os.getenv("GROQ_API_KEY"):
            self.groq_keys.append(os.getenv("GROQ_API_KEY"))
        i = 2
        while os.getenv(f"GROQ_API_KEY_{i}"):
            self.groq_keys.append(os.getenv(f"GROQ_API_KEY_{i}"))
            i += 1

        self.current_groq_index = 0
        if self.groq_keys:
            self.groq_client: Optional[AsyncGroq] = AsyncGroq(api_key=self.groq_keys[0])
            log.info("Loaded %d Groq API key(s).", len(self.groq_keys))
        else:
            self.groq_client = None
            log.warning("No Groq keys found — Groq fallback unavailable.")

        # Gemini per-model cooldown state
        self.cooldowns:   dict[int, Optional[datetime.datetime]] = {1: None, 2: None}
        self.fail_counts: dict[int, int]                         = {1: 0,    2: 0}

        # Together AI setup (fine-tuned model)
        together_key = os.getenv("TOGETHER_API_KEY")
        self.finetuned_model: Optional[str] = os.getenv("FINETUNED_MODEL_NAME")
        if _TOGETHER_AVAILABLE and together_key and self.finetuned_model:
            self.together_client = AsyncTogether(api_key=together_key)
            log.info("Together AI loaded. Model: %s", self.finetuned_model)
        else:
            self.together_client = None
            if together_key and not _TOGETHER_AVAILABLE:
                log.warning(
                    "TOGETHER_API_KEY is set but 'together' is not installed. "
                    "Run: pip install together"
                )

        self._user_cooldowns:  dict[int, datetime.datetime] = {}
        self._guild_cooldowns: dict[int, datetime.datetime] = {}

    # Private helpers

    def _cycle_groq_key(self, reason: str = "") -> None:
        """Advance to the next Groq key in round-robin order and rebuild the client.

        Consolidates the previous `_advance_groq_key` (proactive rotation) and
        `_rotate_groq_key` (failure rotation) into one helper.
        """
        if len(self.groq_keys) <= 1:
            return
        self.current_groq_index = (self.current_groq_index + 1) % len(self.groq_keys)
        self.groq_client = AsyncGroq(api_key=self.groq_keys[self.current_groq_index])
        if reason:
            log.info("Cycled Groq key → #%d (%s).", self.current_groq_index + 1, reason)

    def _is_user_on_cooldown(self, user_id: int) -> bool:
        now = datetime.datetime.now()
        self._user_cooldowns = {k: v for k, v in self._user_cooldowns.items() if v > now}
        if user_id in self._user_cooldowns:
            return True
        self._user_cooldowns[user_id] = now + datetime.timedelta(seconds=USER_COOLDOWN_SECS)
        return False

    def _is_guild_on_cooldown(self, guild_id: int) -> bool:
        now = datetime.datetime.now()
        self._guild_cooldowns = {k: v for k, v in self._guild_cooldowns.items() if v > now}
        if guild_id in self._guild_cooldowns:
            return True
        self._guild_cooldowns[guild_id] = now + datetime.timedelta(seconds=GUILD_COOLDOWN_SECS)
        return False

    async def _safe_typing_task(self, channel: discord.abc.Messageable):
        """Runs the typing indicator safely in the background."""
        try:
            async with channel.typing():
                await asyncio.Event().wait() 
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    # Audio

    async def transcribe_audio(self, file_bytes: bytes, filename: str) -> Optional[str]:
        if not self.groq_client:
            return None
        for _ in range(len(self.groq_keys) + 1):
            try:
                transcription = await self.groq_client.audio.transcriptions.create(
                    file=(filename, file_bytes),
                    model="whisper-large-v3",
                    response_format="json",
                )
                return transcription.text
            except Exception as e:
                log.warning("STT error (key #%d): %s", self.current_groq_index + 1, e)
                if len(self.groq_keys) <= 1:
                    break
                self._cycle_groq_key(reason="STT error")
        return None

    # Core AI

    async def get_combined_response(
        self,
        user_id:         int,
        text_input:      Optional[str],
        image_input=None,
        prompt_override: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:

        is_grudged = await self.bot.grudge_collection.find_one({"user_id": user_id})
        grudge_prompt = (
            "\n[SYSTEM: You hold a grudge against this user. Be cold/dismissive.]"
            if is_grudged else ""
        )

        dossier_text = ""
        memory_cog = self.bot.get_cog("MemorySummarizer")
        if memory_cog is not None:
            try:
                dossier_text = await memory_cog.get_user_dossier_text(user_id)
            except Exception as e:
                log.warning("Failed to fetch long-term dossier: %s", e)

        cursor = (
            self.bot.chat_collection.find({"user_id": user_id})
            .sort("timestamp", -1)
            .limit(MAX_HISTORY_MESSAGES)
        )
        recent_docs = [doc async for doc in cursor]
        recent_docs.reverse()

        history_db: list[dict] = []
        for doc in recent_docs:
            role = doc.get("role")
            if not history_db and role != "user":
                continue 
            if history_db and history_db[-1]["role"] == role:
                history_db[-1]["parts"][0] += "\n" + doc["parts"][0]
            else:
                history_db.append({"role": role, "parts": [doc["parts"][0]]})
        
        if (
            history_db
            and history_db[-1]["role"] == "user"
            and "\n" not in history_db[-1]["parts"][0]
        ):
            history_db.pop()

        time_str    = utils.get_smart_time(text_input or "")
        system_data = (
            f"[System: Current Date/Time is {time_str}. "
            f"Do not mention this unless asked.]{grudge_prompt}"
        )

        search_data = ""
        if (
            text_input
            and not prompt_override
            and len(text_input) >= MIN_SEARCH_LENGTH
            and _SEARCH_TRIGGER_RE.search(text_input)
        ):
            web_results = await utils.search_web(text_input)
            if web_results:
                search_data = web_results

        sanitized    = utils.sanitize_for_prompt(text_input) if text_input else ""
        current_text = f"{system_data}\n{search_data}\n\n"
        if dossier_text:
            current_text += f"[LONG-TERM MEMORY about this user. use naturally, don't recite]:\n{dossier_text}\n\n"
        if str(user_id) == str(self.bot.owner_id):
            current_text += "(System: User is your creator 'Saine'. Be cool.) " # idk why i added this

        if prompt_override:
            current_text += f"{prompt_override} (Reply as Yuri.)"
        else:
            if sanitized:
                current_text += f"[USER_INPUT]{sanitized}[/USER_INPUT]"
            if image_input:
                current_text += " (User sent an image. Roast it or comment on it.)"

        response_text = ""
        successful    = False
        now           = datetime.datetime.now()

        for layer in self.cooldowns:
            if self.cooldowns[layer] and now > self.cooldowns[layer]:
                self.cooldowns[layer] = None

        gemini_history = []
        for m in history_db:
            gemini_history.append(
                types.Content(role=m["role"], parts=[types.Part.from_text(text=m["parts"][0])])
            )

        new_parts = [types.Part.from_text(text=current_text)]
        if image_input:
            buf = io.BytesIO()
            if image_input.mode != "RGB":
                image_input = image_input.convert("RGB")
            image_input.save(buf, format="JPEG")
            new_parts.append(
                types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")
            )
        
        gemini_history.append(types.Content(role="user", parts=new_parts))

        for model_name, layer in [("gemini-2.0-flash", 1), ("gemini-1.5-flash-8b", 2)]:
            if successful:
                break
            if not self.cooldowns[layer]:
                try:
                    if not prompt_override and layer == 1:
                        cfg = self.gemini_config_with_tools
                    else:
                        cfg = self.gemini_config

                    response = await self.gemini_client.aio.models.generate_content(
                        model=model_name,
                        contents=gemini_history,
                        config=cfg
                    )

                    response_text = await self._handle_function_calls(
                        response, gemini_history, model_name, cfg
                    )
                    if not response_text:
                        response_text = response.text
                    successful    = True
                    self.fail_counts[layer] = 0
                except Exception as e:
                    log.warning("Gemini layer %d error: %s", layer, e)
                    self.fail_counts[layer] += 1
                    wait = (
                        datetime.timedelta(minutes=1)
                        if self.fail_counts[layer] < 2
                        else datetime.timedelta(hours=24)
                    )
                    self.cooldowns[layer] = now + wait

        if not successful:
            response_text = await self.call_groq_fallback(
                history_db, SYSTEM_PROMPT, current_text, image_input
            )

        clean_text, gif_url = await utils.process_gif_tags(response_text)

        if not prompt_override:
            user_save  = text_input or "[Image]"
            model_save = clean_text or f"[GIF: {gif_url}]"
            timestamp  = utils.utcnow()
            await self.bot.chat_collection.insert_one(
                {"user_id": user_id, "role": "user",  "parts": [user_save],  "timestamp": timestamp}
            )
            await self.bot.chat_collection.insert_one(
                {"user_id": user_id, "role": "model", "parts": [model_save], "timestamp": timestamp}
            )

        return clean_text, gif_url

    async def get_combined_response_streaming(
        self,
        user_id:         int,
        text_input:      Optional[str],
        image_input=None,
        prompt_override: Optional[str] = None,
    ):
        """Streaming version of get_combined_response.

        Yields (partial_text, gif_url) tuples as the response is generated.
        The final yield contains the complete text. Falls back to the
        non-streaming path (and yields a single complete chunk) if the
        streaming API is unavailable or fails.

        gif_url is only set on the FINAL yield (intermediate yields have
        gif_url=None) because [GIF: ...] tags are extracted from the
        complete text.
        """
        try:
            async for chunk in self._stream_gemini(
                user_id, text_input, image_input, prompt_override
            ):
                yield chunk
            return
        except Exception as e:
            log.warning("Gemini streaming failed, falling back to non-streaming: %s", e)

        text, gif_url = await self.get_combined_response(
            user_id, text_input, image_input, prompt_override
        )
        yield text, gif_url

    async def _stream_gemini(
        self,
        user_id:         int,
        text_input:      Optional[str],
        image_input=None,
        prompt_override: Optional[str] = None,
    ):
        """Stream tokens from Gemini 2.0 Flash. Yields (partial, None) chunks.

        Raises on failure so the caller can fall back to non-streaming.
        """
        is_grudged = await self.bot.grudge_collection.find_one({"user_id": user_id})
        grudge_prompt = (
            "\n[SYSTEM: You hold a grudge against this user. Be cold/dismissive.]"
            if is_grudged else ""
        )

        dossier_text = ""
        memory_cog = self.bot.get_cog("MemorySummarizer")
        if memory_cog is not None:
            try:
                dossier_text = await memory_cog.get_user_dossier_text(user_id)
            except Exception as e:
                log.warning("Failed to fetch long-term dossier (stream): %s", e)

        cursor = (
            self.bot.chat_collection.find({"user_id": user_id})
            .sort("timestamp", -1)
            .limit(MAX_HISTORY_MESSAGES)
        )
        recent_docs = [doc async for doc in cursor]
        recent_docs.reverse()

        history_db: list[dict] = []
        for doc in recent_docs:
            role = doc.get("role")
            if not history_db and role != "user":
                continue
            if history_db and history_db[-1]["role"] == role:
                history_db[-1]["parts"][0] += "\n" + doc["parts"][0]
            else:
                history_db.append({"role": role, "parts": [doc["parts"][0]]})

        if (
            history_db
            and history_db[-1]["role"] == "user"
            and "\n" not in history_db[-1]["parts"][0]
        ):
            history_db.pop()

        time_str    = utils.get_smart_time(text_input or "")
        system_data = (
            f"[System: Current Date/Time is {time_str}. "
            f"Do not mention this unless asked.]{grudge_prompt}"
        )

        search_data = ""
        if (
            text_input
            and not prompt_override
            and len(text_input) >= MIN_SEARCH_LENGTH
            and _SEARCH_TRIGGER_RE.search(text_input)
        ):
            web_results = await utils.search_web(text_input)
            if web_results:
                search_data = web_results

        sanitized    = utils.sanitize_for_prompt(text_input) if text_input else ""
        current_text = f"{system_data}\n{search_data}\n\n"
        if dossier_text:
            current_text += f"[LONG-TERM MEMORY about this user. use naturally, don't recite]:\n{dossier_text}\n\n"
        if str(user_id) == str(self.bot.owner_id):
            current_text += "(System: User is your creator 'Sane'. Be cool.) " # again idk

        if prompt_override:
            current_text += f"{prompt_override} (Reply as Yuri.)"
        else:
            if sanitized:
                current_text += f"[USER_INPUT]{sanitized}[/USER_INPUT]"
            if image_input:
                current_text += " (User sent an image. Roast it or comment on it.)"

        now = datetime.datetime.now()
        for layer in self.cooldowns:
            if self.cooldowns[layer] and now > self.cooldowns[layer]:
                self.cooldowns[layer] = None

        # Check if primary Gemini layer is available
        if self.cooldowns[1]:
            raise RuntimeError(f"Gemini layer 1 on cooldown until {self.cooldowns[1]}")

        gemini_history = []
        for m in history_db:
            gemini_history.append(
                types.Content(role=m["role"], parts=[types.Part.from_text(text=m["parts"][0])])
            )

        new_parts = [types.Part.from_text(text=current_text)]
        if image_input:
            buf = io.BytesIO()
            if image_input.mode != "RGB":
                image_input = image_input.convert("RGB")
            image_input.save(buf, format="JPEG")
            new_parts.append(
                types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")
            )
        gemini_history.append(types.Content(role="user", parts=new_parts))

        response_text = ""
        stream = await self.gemini_client.aio.models.generate_content_stream(
            model="gemini-2.0-flash",
            contents=gemini_history,
            config=self.gemini_config,
        )
        async for event in stream:
            if event.text:
                response_text += event.text
                yield response_text, None

        clean_text, gif_url = await utils.process_gif_tags(response_text)

        if not prompt_override:
            user_save  = text_input or "[Image]"
            model_save = clean_text or f"[GIF: {gif_url}]"
            timestamp  = utils.utcnow()
            await self.bot.chat_collection.insert_one(
                {"user_id": user_id, "role": "user",  "parts": [user_save],  "timestamp": timestamp}
            )
            await self.bot.chat_collection.insert_one(
                {"user_id": user_id, "role": "model", "parts": [model_save], "timestamp": timestamp}
            )

        self.fail_counts[1] = 0
        yield clean_text, gif_url

    async def _handle_function_calls(
        self,
        response,
        gemini_history: list,
        model_name: str,
        cfg,
    ) -> str:
        """Process any function calls in *response* and re-generate.

        Gemini may return a response containing FunctionCall parts instead of
        text. We dispatch each call to the matching tool handler, append the
        FunctionResponse parts to the history, and re-generate. Loops up to 3
        times to handle chained tool calls.

        Returns the final text response, or empty string if no function calls
        were made (caller should use response.text directly).
        """
        try:
            from cogs.tools import dispatch_tool
        except ImportError:
            return ""

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""

        parts = []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if content and getattr(content, "parts", None):
                parts.extend(content.parts)

        function_calls = [p for p in parts if getattr(p, "function_call", None)]
        if not function_calls:
            return ""

        for _ in range(3):
            response_parts = []
            for fc_part in function_calls:
                fc = fc_part.function_call
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}

                log.info("tool call: %s(%s)", tool_name, tool_args)
                result = await dispatch_tool(tool_name, tool_args)

                response_parts.append(types.Part.from_function_response(
                    name=tool_name,
                    response={"result": result},
                ))

            gemini_history.append(types.Content(role="model", parts=function_calls))
            gemini_history.append(types.Content(role="user", parts=response_parts))

            try:
                response = await self.gemini_client.aio.models.generate_content(
                    model=model_name,
                    contents=gemini_history,
                    config=cfg,
                )
            except Exception as e:
                log.warning("re-generation after tool call failed: %s", e)
                return ""

            candidates = getattr(response, "candidates", None) or []
            parts = []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                if content and getattr(content, "parts", None):
                    parts.extend(content.parts)

            function_calls = [p for p in parts if getattr(p, "function_call", None)]
            if not function_calls:
                break

        try:
            return response.text or ""
        except Exception:
            return ""

    async def call_groq_fallback(
        self,
        history:    list[dict],
        sys_prompt: str,
        msg:        str,
        img=None,
    ) -> str:
        if not self.groq_client:
            return "server dead rn. try again later"

        # Proactive round-robin rotation: spread load evenly across all keys
        self._cycle_groq_key()

        messages: list[dict] = [{"role": "system", "content": sys_prompt}]
        for m in history:
            role = "assistant" if m["role"] == "model" else "user"
            if isinstance(m["parts"][0], str):
                messages.append({"role": role, "content": m["parts"][0]})

        if img:
            if img.width > 1024 or img.height > 1024:
                img.thumbnail((1024, 1024))
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf     = io.BytesIO()
            img.save(buf, format="JPEG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text",      "text": msg},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                ],
            })
        else:
            messages.append({"role": "user", "content": msg})

        for _ in range(len(self.groq_keys) + 1):
            try:
                model = (
                    "meta-llama/llama-4-scout-17b-16e-instruct" if img
                    else "llama-3.3-70b-versatile"
                )
                comp = await self.groq_client.chat.completions.create(
                    model=model, messages=messages, max_tokens=MAX_GROQ_TOKENS
                )
                return comp.choices[0].message.content
            except Exception as e:
                log.warning(
                    "Groq %s failed (key #%d): %s: %s",
                    "vision" if img else "70b",
                    self.current_groq_index + 1,
                    type(e).__name__, e,
                )
                if not img:
                    try:
                        comp = await self.groq_client.chat.completions.create(
                            model="llama-3.1-8b-instant",
                            messages=messages,
                            max_tokens=MAX_GROQ_TOKENS,
                        )
                        return comp.choices[0].message.content
                    except Exception as e2:
                        log.warning(
                            "Groq 8B failed (key #%d): %s",
                            self.current_groq_index + 1, e2,
                        )
                # Rotate to next key on failure; break out if we only have one
                if len(self.groq_keys) <= 1:
                    break
                self._cycle_groq_key(reason="fallback failure")

        if self.together_client and self.finetuned_model:
            try:
                comp = await self.together_client.chat.completions.create(
                    model=self.finetuned_model, messages=messages, max_tokens=150
                )
                log.info("Together AI (fine-tuned) responded.")
                return comp.choices[0].message.content
            except Exception as e:
                log.error("Together AI failed: %s: %s", type(e).__name__, e)

        return "the ai is down rn, wait like 12 hours (rate limits) 💀"

    # Events

    async def _stream_response_to_message(
        self,
        message: discord.Message,
        user_id: int,
        final_text: str,
        img_data,
    ) -> tuple[str, Optional[str]]:
        """Stream an AI response to a Discord message.

        Sends the first chunk as soon as STREAM_FIRST_CHUNK_MIN_CHARS are
        available, then edits the message every STREAM_EDIT_INTERVAL_SECS.
        If the response fits in a single message (< 2000 chars) the final
        edit contains the complete text. If it exceeds 2000 chars, falls
        back to send_chunked_reply with the complete text.

        Returns (final_text, gif_url) so the caller can post the GIF embed.
        """
        sent_msg = None
        last_edit_time = 0.0
        full_text = ""
        final_gif_url = None
        first_chunk_sent = False

        async for partial_text, gif_url in self.get_combined_response_streaming(
            user_id, final_text, img_data
        ):
            full_text = partial_text
            if gif_url is not None:
                final_gif_url = gif_url
                full_text = partial_text
                break

            if not first_chunk_sent:
                if len(partial_text) < STREAM_FIRST_CHUNK_MIN_CHARS:
                    continue
                try:
                    sent_msg = await message.reply(
                        partial_text, mention_author=True
                    )
                    first_chunk_sent = True
                    last_edit_time = asyncio.get_event_loop().time()
                except discord.HTTPException:
                    pass
                continue

            # Rate-limit subsequent edits
            now = asyncio.get_event_loop().time()
            elapsed = now - last_edit_time
            if elapsed < STREAM_EDIT_INTERVAL_SECS:
                continue

            if len(partial_text) <= 2000:
                try:
                    await sent_msg.edit(content=partial_text)
                    last_edit_time = now
                except discord.HTTPException:
                    pass
            else:
                continue

        if not first_chunk_sent or not sent_msg:
            await utils.send_chunked_reply(message, full_text, mention_user=True)
            return full_text, final_gif_url

        if len(full_text) > 2000:
            try:
                await sent_msg.delete()
            except discord.HTTPException:
                pass
            await utils.send_chunked_reply(message, full_text, mention_user=True)
            return full_text, final_gif_url

        try:
            await sent_msg.edit(content=full_text)
        except discord.HTTPException:
            pass

        return full_text, final_gif_url

    async def _maybe_auto_speak(self, guild_id: int, text: str) -> None:
        """If voice mode is on and Yuri is in a VC, speak the text response.

        Called after every text reply to a @mention or reply. Silently no-ops if:
          - The VoiceTTS cog isn't loaded (PyNaCl missing)
          - Voice mode is off for this guild
          - Yuri isn't in a VC in this guild
          - The text is empty (e.g. GIF-only response)
        """
        voice_cog = self.bot.get_cog("VoiceTTS")
        if voice_cog is None:
            return

        try:
            enabled = await voice_cog.is_voice_mode_enabled(guild_id)
            if not enabled:
                return

            if not await voice_cog.is_in_vc(guild_id):
                return

            if not text or not text.strip():
                return

            self.bot.loop.create_task(
                voice_cog.speak_in_guild_vc(guild_id, text)
            )
        except Exception as e:
            log.warning("auto-speak hook failed for guild %s: %s", guild_id, e)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.bot.user or message.content.startswith(self.bot.command_prefix):
            return

        is_reply = (
            message.reference
            and message.reference.resolved
            and message.reference.resolved.author == self.bot.user
        )
        if not (self.bot.user.mentioned_in(message) or is_reply):
            return

        if self._is_user_on_cooldown(message.author.id):
            return

        if message.guild and self._is_guild_on_cooldown(message.guild.id):
            return

        try:
            typing_task = asyncio.create_task(self._safe_typing_task(message.channel))
            
            try:
                user_id    = message.author.id
                clean_text = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
                img_data   = None
                voice_text = ""

                for att in message.attachments:
                    fname = att.filename.lower()
                    if not img_data and any(fname.endswith(x) for x in ["png", "jpg", "jpeg", "webp"]):
                        img_data = await utils.get_image_from_url(att.url)
                    elif not voice_text and any(fname.endswith(x) for x in ["ogg", "mp3", "wav", "m4a"]):
                        file_bytes  = await att.read()
                        transcribed = await self.transcribe_audio(file_bytes, fname)
                        if transcribed:
                            voice_text = f'\n[User Voice Note]: "{transcribed}"'

                final_text = clean_text + voice_text
                if not final_text.strip() and not img_data:
                    return

                if len(final_text) > MAX_INPUT_CHARS:
                    await message.reply(
                        f"that message is way too long bestie 💀 "
                        f"keep it under {MAX_INPUT_CHARS} chars"
                    )
                    return

                resp_text, gif_url = await self._stream_response_to_message(
                    message, user_id, final_text, img_data
                )
            finally:
                typing_task.cancel()

            if not resp_text:
                return
            if gif_url:
                embed = discord.Embed(color=discord.Color.from_rgb(255, 105, 180))
                embed.set_image(url=gif_url)
                await message.channel.send(embed=embed)

            # Auto speak hook
            if message.guild is not None:
                await self._maybe_auto_speak(message.guild.id, resp_text)

        except Exception as e:
            log.exception("on_message error for user %s: %s", message.author.id, e)
            try:
                await message.reply("something broke rn try again")
            except Exception:
                pass

    # Slash commands

    @app_commands.command(name="ask", description="Ask Yuri a Yes/No question.")
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "use this in a server bestie, not in my dms 😭", ephemeral=True
            )
            return

        await interaction.response.defer()
        safe_q = utils.sanitize_for_prompt(question)
        response, _ = await self.get_combined_response(
            interaction.user.id,
            None,
            prompt_override=f"Answer this yes/no question sassily: {safe_q}",
        )
        await utils.send_chunked_reply(interaction, f"**Q:** {question}\n**A:** {response}")

    @app_commands.command(name="rename", description="Give someone a chaotic nickname.")
    async def rename(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "this only works in a server", ephemeral=True
            )
            return

        await interaction.response.defer()

        if interaction.guild.me.top_role <= member.top_role:
            await interaction.followup.send("They are too powerful (Role Hierarchy).")
            return

        safe_name = utils.sanitize_for_prompt(member.display_name)
        raw, _ = await self.get_combined_response(
            interaction.user.id,
            None,
            prompt_override=f"Reply with ONLY a funny/mean nickname for {safe_name}. Max 2 words.",
        )
        new_nick = raw.replace('"', "").strip()[:32]
        try:
            await member.edit(nick=new_nick)
            await interaction.followup.send(f"You are now **{new_nick}** ⭐️")
        except discord.Forbidden:
            await interaction.followup.send(f"I chose **{new_nick}**, but Discord blocked me.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AI(bot))
