import asyncio
import datetime
import io
import logging
import random
import re

import aiohttp
import discord
import pytz
from duckduckgo_search import DDGS
from PIL import Image

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB download cap
MAX_IMAGE_PIXELS = 5_000 * 5_000  # decompression bomb guard (25 MP)
CHUNK_SIZE = 1_900  # Discord cap is 2000, leave some headroom
HISTORY_MSG_MAX = 400  # truncate long messages instead of dropping them

# datetime.utcnow() is deprecated on 3.12+, everything uses tz-aware now
UTC = datetime.UTC


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(UTC)


# shared aiohttp session, closed on bot shutdown via close_session()
_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "YuriBot/1.0"},
        )
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


_UNICODE_BRACKET_TABLE = str.maketrans(
    {
        "\u3010": "[",  # [
        "\u3011": "]",  # ]
        "\u3014": "[",  # [
        "\u3015": "]",  # ]
        "\u300a": "<",  # <
        "\u300b": ">",  # >
        "\u300c": "[",  # [
        "\u300d": "]",  # ]
        "\uff3b": "[",  # [ (fullwidth)
        "\uff3d": "]",  # ] (fullwidth)
        "\uff1c": "<",  # < (fullwidth)
        "\uff1e": ">",  # > (fullwidth)
    }
)

# Patterns that are only sent by someone trying to manipulate the model,
# never in normal conversation.
_INJECTION_RE = re.compile(
    r"<\s*(system|prompt|inst|assistant|user)\b[^>]*>"  # XML-style tags
    r"|"
    r"\[\s*/?\s*(SYSTEM|INST|PROMPT|ASSISTANT|USER)\s*\]"  # bracket-style tags
    r"|"
    r"\bignore\s+(previous|all|your)\s+instructions?\b",  # natural-language reset
    re.IGNORECASE,
)


# Matches Discord mentions that can ping: @everyone, @here, user/role/channel pings.
# We break the @ symbol with a zero-width space so Discord won't render them as pings.
_MENTION_RE = re.compile(
    r"@(everyone|here)\b"  # @everyone / @here
    r"|"
    r"<@!?\d+>"  # <@123> / <@!123>  (user)
    r"|"
    r"<@&\d+>"  # <@&123>           (role)
    r"|"
    r"<#\d+>"  # <#123>            (channel)
)


def sanitize_for_discord(text: str) -> str:
    """Neutralise every form of Discord mention in user-supplied text.

    Anything interpolated into an embed or message body (confessions, hotornot
    descriptions, ...) must go through this or it can ping @everyone/roles.
    """
    if not text:
        return ""
    text = str(text)

    def _break(match: re.Match) -> str:
        token = match.group(0)
        # zero-width space after the @ or < so Discord renders it but it
        # doesn't trigger a notification
        if token.startswith("@"):
            return "@\u200b" + token[1:]
        return "<\u200b" + token[1:]

    return _MENTION_RE.sub(_break, text)


def sanitize_for_prompt(text: str) -> str:
    """Escape user input before it goes anywhere near a model prompt."""
    if not text:
        return ""

    text = str(text)

    # normalise unicode lookalikes to ASCII so the patterns below work
    text = text.translate(_UNICODE_BRACKET_TABLE)

    if _INJECTION_RE.search(text):
        log.warning("Prompt injection attempt detected and blocked.")
        return "[message removed: injection attempt detected]"

    text = text.replace("[", r"\[").replace("]", r"\]")
    text = text.replace("<", "&lt;").replace(">", "&gt;")

    return text


async def get_image_from_url(url: str) -> Image.Image | None:
    """Download an image with a hard size cap, None on failure or oversize."""
    try:
        session = get_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                return None

            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                log.warning("Image rejected: Content-Length %s exceeds limit.", content_length)
                return None

            data = bytearray()
            async for chunk in resp.content.iter_chunked(1024):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    log.warning("Image rejected: streamed size exceeded %d bytes.", MAX_IMAGE_BYTES)
                    return None

            return Image.open(io.BytesIO(data))

    except Exception as e:
        log.warning("Image download error from %s: %s", url, e)
        return None


def stitch_images(img1_data: Image.Image, img2_data: Image.Image) -> Image.Image | None:
    """Combine two PIL images side-by-side at a standard height of 512 px."""
    try:
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

        if (
            img1_data.width > 5_000
            or img1_data.height > 5_000
            or img2_data.width > 5_000
            or img2_data.height > 5_000
        ):
            log.warning("stitch_images: source image exceeds the size limit.")
            return None

        base_height = 512

        ratio1 = base_height / float(img1_data.size[1])
        w1 = int(float(img1_data.size[0]) * ratio1)
        img1 = img1_data.resize((w1, base_height), Image.Resampling.BICUBIC)

        ratio2 = base_height / float(img2_data.size[1])
        w2 = int(float(img2_data.size[0]) * ratio2)
        img2 = img2_data.resize((w2, base_height), Image.Resampling.BICUBIC)

        result = Image.new("RGB", (w1 + w2, base_height))
        result.paste(img1, (0, 0))
        result.paste(img2, (w1, 0))
        return result

    except Exception as e:
        log.error("stitch_images error: %s", e)
        return None


def get_smart_time(text_input: str) -> str:
    """Guess the user's timezone from their message language and return local time."""
    utc_now = datetime.datetime.now(pytz.utc)

    # Hindi / Hinglish / Bengali script or common Hinglish words -> IST
    if (
        re.search(r"[\u0900-\u097F]", text_input)  # Devanagari
        or re.search(r"[\u0980-\u09FF]", text_input)  # Bengali
        or any(
            word in text_input.lower()
            for word in ["kya", "kab", "hai", "bhai", "samay", "baj", "baje"]
        )
    ):
        local = utc_now.astimezone(pytz.timezone("Asia/Kolkata"))
        return f"{local.strftime('%I:%M %p')} (IST)"

    # Japanese script -> JST
    if re.search(r"[\u3040-\u309F\u30A0-\u30FF]", text_input):
        local = utc_now.astimezone(pytz.timezone("Asia/Tokyo"))
        return f"{local.strftime('%I:%M %p')} (JST)"

    local = utc_now.astimezone(pytz.timezone("Asia/Kolkata"))
    return f"{local.strftime('%A, %B %d, %I:%M %p')} (IST)"


async def search_web(query: str) -> str | None:
    """DuckDuckGo text search, formatted as a context block for the model."""
    try:
        results = await asyncio.to_thread(lambda: list(DDGS().text(query, max_results=2)))
        if not results:
            return None

        context = "\n[SYSTEM: WEB SEARCH RESULTS]\n"
        for res in results:
            context += (
                f"- Title: {sanitize_for_prompt(res['title'])}\n"
                f"  Snippet: {sanitize_for_prompt(res['body'])}\n"
            )
        return context

    except Exception as e:
        log.warning("Web search error for query '%s': %s", query, e)
        return None


async def search_gif_ddg(query: str) -> str | None:
    """Search DuckDuckGo Images for a GIF and return a random result URL."""
    try:
        results = await asyncio.to_thread(
            lambda: list(DDGS().images(keywords=query, type_image="gif", max_results=8))
        )
        if results:
            return random.choice(results)["image"]
    except Exception as e:
        log.warning("GIF search error for query '%s': %s", query, e)
    return None


async def process_gif_tags(text: str) -> tuple[str, str | None]:
    """Extract a [GIF: ...] tag, search for the GIF, strip the tag from text."""
    match = re.search(r"\[GIF:\s*(.*?)\]", text, re.IGNORECASE)
    gif_url: str | None = None
    if match:
        query = match.group(1).strip()
        gif_url = await search_gif_ddg(query)
        text = text.replace(match.group(0), "").strip()
    return text, gif_url


async def fetch_channel_messages(
    channel: discord.abc.Messageable,
    *,
    fetch_limit: int = 100,
    keep_limit: int = 20,
    timeout_secs: float = 8.0,
) -> list[str]:
    """Recent non-bot messages as sanitised "Name: message" strings, oldest first.

    Returns whatever was collected even on timeout or error.
    """
    messages: list[str] = []
    try:
        async with asyncio.timeout(timeout_secs):
            async for msg in channel.history(limit=fetch_limit):
                if msg.author.bot:
                    continue
                safe = sanitize_for_prompt(msg.content)
                if safe.strip():
                    messages.append(f"{msg.author.display_name}: {safe}")
                if len(messages) >= keep_limit:
                    break
    except TimeoutError:
        log.warning(
            "fetch_channel_messages timed out after %.1fs in channel %s, "
            "returning %d message(s) collected so far.",
            timeout_secs,
            getattr(channel, "id", "?"),
            len(messages),
        )
    except Exception as e:
        log.warning(
            "fetch_channel_messages error in channel %s: %s", getattr(channel, "id", "?"), e
        )

    messages.reverse()  # oldest -> newest
    return messages


async def send_chunked_reply(
    destination,
    text: str,
    *,
    mention_user: bool = False,
) -> None:
    """Send *text* to a Message / Interaction / channel, splitting past 2000 chars."""
    if not text:
        return

    chunks = [text[i : i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]

    for i, chunk in enumerate(chunks):
        try:
            if hasattr(destination, "reply") and i == 0:
                await destination.reply(chunk, mention_author=mention_user)
            elif hasattr(destination, "followup"):
                await destination.followup.send(chunk)
            elif hasattr(destination, "send"):
                await destination.send(chunk)
            else:
                await destination.channel.send(chunk)
        except Exception as e:
            log.warning("send_chunked_reply failed on chunk %d: %s", i, e)


def get_user_dossier(member: discord.Member, include_presence: bool = True) -> str:
    """Short text profile of *member* for AI prompts.

    Presence details (Spotify, games, custom status) are left out when the
    user opted out via /privacy.
    """
    now = utcnow()
    # member.created_at is tz-aware in discord.py, guard just in case
    created_at = (
        member.created_at if member.created_at.tzinfo else member.created_at.replace(tzinfo=UTC)
    )
    age_days = (now - created_at).days
    years = age_days // 365

    roles = [r.name for r in member.roles if r.name != "@everyone"]
    roles_str = sanitize_for_prompt(", ".join(roles) if roles else "No Roles")

    if not include_presence:
        return (
            f"METADATA (Use ONLY if funny):\n"
            f"- Name: {sanitize_for_prompt(member.display_name)}\n"
            f"- Account Age: {years} year(s), {age_days % 365} day(s) old.\n"
            f"- Roles: {roles_str}\n"
            f"- Status: (hidden by user privacy opt-out)\n"
        )

    status = str(member.status).upper()

    activity = "None"
    if member.activity:
        if isinstance(member.activity, discord.Spotify):
            activity = (
                f"Listening to {sanitize_for_prompt(member.activity.title)} "
                f"by {sanitize_for_prompt(member.activity.artist)}"
            )
        elif isinstance(member.activity, discord.Game):
            activity = f"Playing {sanitize_for_prompt(member.activity.name)}"
        elif isinstance(member.activity, discord.CustomActivity):
            activity = f"Custom Status: '{sanitize_for_prompt(str(member.activity.name))}'"

    return (
        f"METADATA (Use ONLY if funny):\n"
        f"- Name: {sanitize_for_prompt(member.display_name)}\n"
        f"- Account Age: {years} year(s), {age_days % 365} day(s) old.\n"
        f"- Roles: {roles_str}\n"
        f"- Status: {status} | Doing: {activity}\n"
    )


async def get_user_history_text(
    collection,
    user_id: int,
    *,
    limit: int = 15,
) -> str:
    """Recent conversation (user + Yuri turns) formatted for a social-command prompt."""
    cursor = (
        collection.find({"user_id": user_id}, {"parts": 1, "role": 1, "_id": 0})
        .sort("timestamp", -1)
        .limit(limit)
    )
    messages: list[str] = []
    async for doc in cursor:
        content = doc.get("parts", [""])[0]
        role = doc.get("role", "user")
        label = "Yuri" if role == "model" else "User"
        if isinstance(content, str) and content.strip():
            # long messages usually carry the most context, truncate, don't drop
            if len(content) > HISTORY_MSG_MAX:
                content = content[:HISTORY_MSG_MAX] + "…"
            messages.append(f"{label}: {sanitize_for_prompt(content)}")

    if not messages:
        return "No recent chat history found."

    return "\n".join(f"- {m}" for m in reversed(messages))
