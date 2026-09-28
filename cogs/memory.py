"""Long-term memory: summarize chat history into permanent per-user dossiers.

A 6-hour sweep finds messages older than SUMMARIZE_AGE_DAYS (before the 30-day
TTL eats them), summarizes them with Gemini, and appends the summary to the
user's dossier in `memory_dossiers`. The AI cog prepends the dossier to the
prompt so Yuri remembers people forever at O(1) storage per user.
"""

import datetime
import logging
import os

from discord.ext import commands, tasks
from google import genai
from google.genai import types

import utils

log = logging.getLogger(__name__)


# must stay under the 30-day chat history TTL
SUMMARIZE_AGE_DAYS = 25
SWEEP_INTERVAL_HOURS = 6
MAX_MESSAGES_PER_SUMMARY = 60
MAX_DOSSIER_CHARS = 2000  # append-based, so cap total growth
SUMMARY_MODEL = "gemini-2.0-flash"


class MemorySummarizer(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._gemini_client: genai.Client | None = None  # lazy, shares the AI cog's key
        self._sweep.start()

    def cog_unload(self) -> None:
        self._sweep.cancel()

    @property
    def gemini_client(self) -> genai.Client | None:
        if self._gemini_client is None:
            key = os.getenv("GEMINI_API_KEY")
            if key:
                self._gemini_client = genai.Client(api_key=key)
        return self._gemini_client

    async def get_user_dossier_text(self, user_id: int) -> str:
        """Stored dossier for *user_id*, empty string if there is none."""
        doc = await self.bot.memory_dossiers_col.find_one(
            {"user_id": user_id}, {"dossier": 1, "_id": 0}
        )
        if doc is None:
            return ""
        return doc.get("dossier", "")

    @tasks.loop(hours=SWEEP_INTERVAL_HOURS)
    async def _sweep(self) -> None:
        try:
            await self._run_summarization_pass()
        except Exception as e:
            log.warning("memory summarization sweep error: %s", e)

    @_sweep.before_loop
    async def _before_sweep(self) -> None:
        await self.bot.wait_until_ready()

    async def _run_summarization_pass(self) -> None:
        """Summarize unsummarized old messages, grouped by user."""
        cutoff = utils.utcnow() - datetime.timedelta(days=SUMMARIZE_AGE_DAYS)

        pipeline = [
            {
                "$match": {
                    "timestamp": {"$lt": cutoff},
                    "summarized": {"$ne": True},
                }
            },
            {"$group": {"_id": "$user_id"}},
            {"$limit": 50},  # cap work per pass
        ]
        cursor = self.bot.chat_collection.aggregate(pipeline)
        user_ids = [doc["_id"] async for doc in cursor]

        if not user_ids:
            return

        log.info("memory summarization: processing %d user(s)", len(user_ids))

        for user_id in user_ids:
            try:
                await self._summarize_user_history(user_id, cutoff)
            except Exception as e:
                log.warning("memory summarization failed for user %s: %s", user_id, e)

    async def _summarize_user_history(self, user_id: int, cutoff: datetime.datetime) -> None:
        """Summarize old messages for a single user and append to their dossier."""
        cursor = (
            self.bot.chat_collection.find(
                {
                    "user_id": user_id,
                    "timestamp": {"$lt": cutoff},
                    "summarized": {"$ne": True},
                },
                {"parts": 1, "role": 1, "timestamp": 1, "_id": 1},
            )
            .sort("timestamp", 1)
            .limit(MAX_MESSAGES_PER_SUMMARY)
        )
        docs = [doc async for doc in cursor]
        if not docs:
            return

        # transcript for the summarizer + the ids to mark done
        transcript_lines = []
        doc_ids = []
        for doc in docs:
            role = "Yuri" if doc.get("role") == "model" else "User"
            content = doc.get("parts", [""])[0]
            if isinstance(content, str) and content.strip():
                transcript_lines.append(f"{role}: {content[:300]}")
            doc_ids.append(doc["_id"])

        if not transcript_lines:
            # nothing worth summarizing, just mark them done
            await self.bot.chat_collection.update_many(
                {"_id": {"$in": doc_ids}},
                {"$set": {"summarized": True}},
            )
            return

        transcript = "\n".join(transcript_lines)

        existing_dossier = await self.get_user_dossier_text(user_id)

        new_summary = await self._generate_summary(transcript, existing_dossier)
        if not new_summary:
            return  # failed, retry next pass

        if existing_dossier:
            updated_dossier = (existing_dossier + "\n\n" + new_summary)[:MAX_DOSSIER_CHARS]
        else:
            updated_dossier = new_summary[:MAX_DOSSIER_CHARS]

        await self.bot.memory_dossiers_col.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "dossier": updated_dossier,
                    "updated_at": utils.utcnow(),
                }
            },
            upsert=True,
        )

        await self.bot.chat_collection.update_many(
            {"_id": {"$in": doc_ids}},
            {"$set": {"summarized": True}},
        )

        log.info(
            "memory summarization: updated dossier for user %s (%d messages summarized)",
            user_id,
            len(doc_ids),
        )

    async def _generate_summary(self, transcript: str, existing_dossier: str) -> str | None:
        """Summarize a transcript into a dossier entry, written as Yuri."""
        client = self.gemini_client
        if client is None:
            return None

        prompt = (
            "You are Yuri, a chaotic gen-z Discord bot. Below is a transcript of "
            "your recent conversation with a user. Summarize it into a short "
            "(3-5 sentence) 'dossier' entry written from your perspective: your "
            "impression of them, the vibe, memorable things they said, how you "
            "two get along. Keep it lowercase, gen-z, in-character. This dossier "
            "will be used as long-term memory so you can remember them later.\n\n"
        )
        if existing_dossier:
            prompt += "EXISTING DOSSIER (build on this, don't repeat):\n" f"{existing_dossier}\n\n"
        prompt += f"NEW CONVERSATION TRANSCRIPT:\n{transcript}\n\n"
        prompt += "DOSSIER ENTRY:"

        try:
            config = types.GenerateContentConfig(
                max_output_tokens=300,
                temperature=0.7,
            )
            response = await client.aio.models.generate_content(
                model=SUMMARY_MODEL,
                contents=prompt,
                config=config,
            )
            text = response.text.strip()
            return text if text else None
        except Exception as e:
            log.warning("memory summarization: Gemini call failed: %s", e)
            return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemorySummarizer(bot))
