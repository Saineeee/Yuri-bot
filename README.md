# 🌸 Yuri

![Python Version](https://img.shields.io/badge/Python-3.11-blue)
![Discord.py](https://img.shields.io/badge/discord.py-v2.0+-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Tests](https://img.shields.io/badge/tests-139%20passing-brightgreen)

Yuri is a highly interactive, chaotic, and dramatic Gen Z AI Discord bot. Unlike standard, polite AI assistants, Yuri is designed with a specific, opinionated teenage persona. She will roast you, judge your vibe, gossip, and dramatically deny being an AI if accused.

Beneath her chaotic personality is a robust, multi-model AI architecture powered by Google Gemini and Groq, featuring streaming responses, function-calling tools, conversation memory with long-term summarization, image vision, voice note transcription, voice channel TTS, image generation, and dynamic social commands.

---

## ✨ Core Features

* **🧠 Multi-Model AI Brain:** Uses Google Gemini (2.0-flash & 1.5-flash-8b) as the primary engine, with automatic fallback to a rotating key system of Groq Llama models. Optionally supports a **custom fine-tuned model** via Together AI as a final last-resort fallback.
* **⚡ Streaming Responses:** Replies stream token-by-token - the first chunk appears in ~500ms instead of waiting for the full response.
* **🔧 Function Calling Tools:** Gemini can autonomously call tools (`web_search`, `get_time_in_timezone`, `calculate`) when needed, replacing the old regex-based search heuristic.
* **💾 Dual-Layer Memory:** 30-day rolling chat history (auto-purged via MongoDB TTL) **plus** permanent long-term dossiers - Yuri summarizes old conversations so she remembers you forever without unbounded DB growth.
* **👁️ Vision & Audio Processing:** Can "see" image attachments and transcribe voice notes using Groq's Whisper model.
* **🔊 Voice Channel TTS:** Join a VC with `/vc join`, enable `/voice on`, and Yuri will speak her replies out loud - just @mention her normally, no `/say` needed.
* **🎨 Image Generation:** `/imagine [prompt]` generates images via Google Imagen, with Yuri adding her own spin.
* **🎭 Social & Drama Systems:** Anonymous confessions, secret crush matching, hot-or-not voting, starboard, personalized roasts based on profile + chat history.
* **📊 Levelling & XP:** Messages earn XP, level-up role rewards, leaderboard, rank cards.
* **🛡️ Defensive Prompting:** Built-in prompt-injection defenses. If users try to "reprogram" her using system tags, she will actively mock them.
* **🔒 Privacy-First:** Per-user `/privacy` opt-out, `/export` for GDPR data portability, `/clearhistory` for instant deletion.

---

## 🤖 AI Fallback Chain

When one provider fails or hits a rate limit, Yuri automatically falls through to the next tier with no interruption:

```
1. Gemini 2.0 Flash (streaming)  ← Primary
2. Gemini 1.5 Flash 8B           ← Secondary Gemini
3. Groq Llama 3.3 70B            ← Groq (round-robin across all loaded keys)
4. Groq Llama 3.1 8B             ← Groq lite
5. Together AI (fine-tuned)      ← Optional last resort
```

Groq keys rotate proactively on every call (not just on failure), so load is spread evenly across all keys you provide. The Together AI tier only activates if `TOGETHER_API_KEY` and `FINETUNED_MODEL_NAME` are set - otherwise it is skipped silently.

---

## 📜 Command Menu

### 💬 Chatting
Yuri doesn't need commands to chat! Just `@mention` her or reply to one of her messages to talk. Send images for her to judge, or voice notes for her to transcribe and reply to. Responses stream in real-time.

### 👀 Judgment & Social
* `/roast @user` - Yuri analyzes the user's profile and chat history to absolutely destroy their ego.
* `/rate @user` - Judges a user's vibe (0-100%) based on their recent chat logs.
* `/ship @user1 [@user2]` - Checks the compatibility between two users, complete with a stitched image of their avatars.
* `/compatibility @user1 [@user2]` - Deep compatibility analysis referencing actual message history.
* `/summarize` - Yuri recaps the last 20 messages in the channel.
* `/rename @user` - Gives someone a cursed, chaotic nickname.

### 🔥 Drama & Chaos
* `/confess [message]` - Sends a completely anonymous confession to a designated server channel. (5-min cooldown)
* `/crush @user` - Secretly matches with someone. If they run the command on you too, Yuri DMs both of you!
* `/truth` - Get a spicy, chaotic teenage Truth question.
* `/dare` - Get a chaotic Dare.
* `/poll [question] [opt1] [opt2]` - Yuri hosts a poll and picks a side.
* `/hotornot [description]` - Anonymous submission. Server judges you - verdict posted after 15 minutes. (10-min cooldown)
* `/8ball [question]` - Magic 8-ball with 20 sassy responses.
* `/avatar [@user]` - Show someone's full-size avatar with size links.

### 🎨 Creative
* `/imagine [prompt]` - Generate an image from a text prompt via Google Imagen. Yuri enhances the prompt and adds commentary. (30-sec cooldown)
* `/translate [text] [language]` - Translate text into 18 languages in Yuri's voice.

### 🔊 Voice
* `/vc [join|leave]` - Make Yuri join or leave your voice channel.
* `/voice [on|off|status]` - Toggle auto-speak. When ON (default), Yuri speaks her @mention replies in the VC - no `/say` needed.
* `/say [text]` - Force Yuri to say something specific out loud in your voice channel. (10-sec cooldown)

### 🧠 Utility
* `/ask [question]` - Ask Yuri a direct yes/no question for a sassy answer.
* `/remind [time] [message]` - Set a reminder. Yuri DMs you when it's time (falls back to channel ping if DMs closed). Time formats: `30m`, `2h`, `1d`, `1h30m`, `45s`, or bare minutes.
* `/history` - View your last few messages with Yuri (ephemeral).
* `/export` - Download your full chat history as JSON (GDPR data portability).
* `/clearhistory` - Wipe your own chat history with Yuri.
* `/forgive [message]` - Apologize and ask Yuri to reset her attitude toward you.
* `/mood` - How does Yuri feel about you right now? (grudge/crush status)
* `/privacy [setting]` - Control what data Yuri uses about you (presence opt-out).
* `/status` - Check if Yuri is online and operational (public, minimal info).
* `/feedback` - Report a bug or suggest a feature directly to the developer.

### 📊 Levelling
* `/rank [@user]` - Show your level, XP, and server rank with a progress bar.
* `/leaderboard` - Top 10 chatters in this server.
* `/rankroles [level] [role] [add|remove]` - Admin: configure role rewards for leveling up.

### 🪽 Admin Only
* `/setup [channel]` - Sets the channel where anonymous confessions are posted.
* `/grudge @user` - Forces Yuri to hold a permanent grudge against a user.
* `/ungrudge @user` - Forgives a user.
* `/wipe @user` - *(Owner only)* Erase a user's conversational history.
* `/starboard [setup|disable]` - Configure a starboard channel for highlighted messages.
* `/setuproles` - Create a reaction-role message (users react to get roles).
* `/health` - *(Owner only, prefix `!`)* Detailed system health with full internals.

### ⚙️ Owner Commands (prefix `!`)
All prefix commands are owner-restricted (`@commands.is_owner()`).
* `!sync` - Syncs slash commands globally after any schema change.
* `!health` - Detailed system health (ping, DB, AI providers, cog list).
* `!stats` - Bot-wide usage statistics across all servers.
* `!inbox` - Lists all feedback submissions (DM-only - prevents public leaks).
* `!reply <user_id> <message>` - DMs a formatted response to a feedback submission.
* `!fetchlog <user_id>` - Downloads a user's full conversation log (DM-only).
* `!dailylog` - Downloads today's interaction log (DM-only).
* `!wipeall confirm` - Clears the entire chat history collection (requires `confirm` arg).
* `!usercount` - Count of unique users currently in the database.

---

## 🛠️ Tech Stack

* **Language:** Python 3.11+
* **Discord Library:** `discord.py` (with voice support via PyNaCl)
* **Database:** MongoDB (`motor` for async, 13 collections)
* **AI APIs:**
  * `google-genai` (Gemini 2.0 Flash + Imagen 3)
  * `groq` (Llama 3.3 70B, Llama 4 Scout vision, Whisper STT, PlayAI TTS)
  * `together` *(optional - fine-tuned model fallback)*
* **Image Processing:** `Pillow` (PIL)
* **Web Search:** `duckduckgo-search`
* **TTS:** Groq PlayAI (primary) + gTTS (fallback)
* **Error Tracking:** Sentry *(optional - `SENTRY_DSN`)*

---

## 📁 Project Structure

```text
yuri-bot/
├── main.py                      # Bot init, MongoDB, Sentry, sharding, cog auto-discovery
├── utils.py                     # Shared helpers: sanitization, images, web search, GIFs
├── requirements.txt             # Pinned dependencies
├── runtime.txt                  # Python 3.11 pin
├── Procfile                     # Heroku/Railway deployment
├── Dockerfile                   # Multi-stage Docker build (ffmpeg included)
├── docker-compose.yml           # Docker Compose with optional local MongoDB
├── pyproject.toml               # Ruff + black + pytest config
├── .env.example                 # Template for environment variables
├── .github/workflows/ci.yml     # GitHub Actions: lint + test on Python 3.11 & 3.12
│
├── cogs/
│   ├── prompts.py               # Yuri's system prompt
│   ├── ai.py                    # Inference pipeline, streaming, function calling, auto-speak
│   ├── tools.py                 # Function-calling tools (web_search, timezone, calculate)
│   ├── memory.py                # Long-term memory summarization (6-hour sweep)
│   ├── social.py                # /roast, /ship, /confess, /crush, /poll, /hotornot, /summarize
│   ├── general.py               # /help, /feedback, /history, /forgive, /mood, /8ball, /translate, /avatar
│   ├── admin.py                 # /status (public), !health (owner), /export, /privacy, owner commands
│   ├── reminders.py             # /remind + 15-sec sweep loop (DM delivery)
│   ├── reactionroles.py         # /setuproles + on_raw_reaction_add/remove listeners
│   ├── imagegen.py              # /imagine (Google Imagen)
│   ├── voicetts.py              # /say, /vc, /voice + auto-speak hook
│   ├── starboard.py             # /starboard + ⭐ reaction tracking
│   └── levelling.py             # /rank, /leaderboard, /rankroles + XP on every message
│
└── tests/
    ├── test_ai.py               # Fallback chain, key rotation, search triggers, sanitisation
    ├── test_admin.py            # /status + !health + owner restriction
    ├── test_utils.py            # Image download guards, stitch dimensions, smart time
    ├── test_social.py           # sanitize_for_discord, shared session, utcnow, history truncation
    ├── test_general.py          # Source-level assertions for fixes
    ├── test_reminders.py        # Time parser, reaction-role parsing, new command assertions
    ├── test_tools.py            # calculate, get_time_in_timezone, dispatch_tool, registry
    ├── test_levelling.py        # XP curve math, level_from_xp round-trips
    ├── test_voicetts.py         # /voice toggle, auto-speak hook, all edge cases
    └── test_new_features.py     # Source-level assertions for all new cogs + infra
```

---

## 🚀 Setup & Installation

### 1. Prerequisites
* Python 3.11+
* A MongoDB cluster (e.g., MongoDB Atlas)
* A Discord Bot Token with **Message Content** and **Server Members** intents enabled
* API keys for Google Gemini and Groq
* *(Optional)* ffmpeg installed for voice channel TTS

### 2. Clone and Install
```bash
git clone https://github.com/Saineeee/Yuri-bot.git
cd yuri-bot
pip install -r requirements.txt
```

### 3. Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```dotenv
# Required
DISCORD_TOKEN=
MONGO_URL=
OWNER_ID=                    # Your Discord user ID (integer)
GEMINI_API_KEY=
GROQ_API_KEY=

# Optional - Groq key rotation
GROQ_API_KEY_2=
GROQ_API_KEY_3=

# Optional - Fine-tuned model via Together AI
TOGETHER_API_KEY=
FINETUNED_MODEL_NAME=

# Optional - Sentry error tracking
SENTRY_DSN=

# Optional - Sharding (for large bots, >1000 servers)
# SHARD_COUNT=1
```

**Configuration reference:**

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Bot token from the Discord Developer Portal |
| `MONGO_URL` | ✅ | MongoDB connection string; Atlas SRV format supported |
| `OWNER_ID` | ✅ | Discord user ID granted owner-only commands |
| `GEMINI_API_KEY` | ✅ | Google AI Studio key for Gemini + Imagen access |
| `GROQ_API_KEY` | ✅ | Primary Groq key (inference, Whisper STT, PlayAI TTS) |
| `GROQ_API_KEY_2` … `_N` | ➖ | Extra Groq keys; round-robin load balancing |
| `TOGETHER_API_KEY` | ➖ | Together AI key; omitting disables the fine-tuned tier |
| `FINETUNED_MODEL_NAME` | ➖ | Together AI model ID - both vars must be set together |
| `SENTRY_DSN` | ➖ | Sentry DSN for error tracking; omitting disables it |
| `SHARD_COUNT` | ➖ | Number of shards; omit for auto-sharding |

### 4. Run the Bot

**Locally:**
```bash
python main.py
```

**With Docker:**
```bash
docker compose up -d
```

After first startup, run `!sync` in any server channel (owner only) to register slash commands globally.

> **Deploying to Railway/Heroku?** A `Procfile` is already included. Set env vars in the platform's config vars dashboard instead of a `.env` file.

---

## 🐳 Docker

A multi-stage Dockerfile is included with ffmpeg pre-installed for voice support:

```bash
# Build and run with the included docker-compose (includes local MongoDB)
docker compose up -d

# View logs
docker compose logs -f bot

# Stop
docker compose down
```

The Docker image runs as a non-root user and includes a health check.

---

## 🔧 Development

### Linting & Formatting
```bash
# Check with ruff
ruff check .

# Format with black
black .

# Check formatting without changing
black --check --diff .
```

### Running Tests
No live credentials required - all external I/O is mocked.

```bash
# Run all 139 tests
python -m pytest tests/ -v

# With coverage
python -m pytest tests/ --cov=cogs --cov=utils.py --cov=main.py
```

### CI/CD
GitHub Actions (`.github/workflows/ci.yml`) runs on every push/PR to `main`/`dev`:
- Lint with ruff
- Format check with black
- Test on Python 3.11 + 3.12
- Build Docker image on `main` branch

---

## 🧬 Fine-Tuning Your Own Model (Optional)

The Together AI slot lets you plug in a model trained on Yuri's own conversation data, giving the most on-character responses during provider outages.

### 1. Export Training Data
```python
import json, asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def export():
    col = AsyncIOMotorClient(MONGO_URL)["yuri_bot_db"]["chat_history"]
    async for doc in col.find({}).sort([("user_id", 1), ("timestamp", 1)]):
        role = "assistant" if doc["role"] == "model" else "user"
        print(json.dumps({"role": role, "content": doc["parts"][0]}))

asyncio.run(export())
```

Group adjacent messages into conversation pairs before uploading. Filter out any messages stored as `[message removed: injection attempt detected]`.

### 2. Fine-Tune on Together AI
1. Upload the JSONL dataset at [together.ai/fine-tuning](https://www.together.ai/fine-tuning).
2. Select a Llama 3 base model and set Yuri's system prompt as the training system message.
3. Start the job (typically 30-90 min for small datasets) and copy the resulting model name.

### 3. Activate
Add both vars to your `.env` and restart - no code changes needed:
```dotenv
TOGETHER_API_KEY=your_key
FINETUNED_MODEL_NAME=your-org/yuri-llama3-ft
```

---

## 🩺 Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Bot ignores `@` mentions | Missing privileged intents | Enable **Message Content** + **Server Members** in Discord Developer Portal → Bot |
| Slash commands not showing | Tree not synced | Run `!sync` as the bot owner |
| `FATAL: MongoDB Connection Failed` | Bad connection string or IP not allowlisted | Verify `MONGO_URL`; whitelist your host IP in Atlas → Network Access |
| All responses return rate-limit message | All provider tiers exhausted | Add more `GROQ_API_KEY_N` keys; wait for Gemini 24h cooldown to reset |
| Together AI tier never activates | One or both env vars missing | Both `TOGETHER_API_KEY` and `FINETUNED_MODEL_NAME` must be set |
| `Role Hierarchy` error on `/rename` | Bot role below target's highest role | Drag Yuri's role above target roles in Server Settings → Roles |
| Voice TTS cog doesn't load | PyNaCl not installed | `pip install PyNaCl` (included in requirements.txt) |
| `/imagine` fails | Imagen API not enabled on your Gemini key | Enable Imagen access in Google AI Studio |
| `/say` or auto-speak silent | Bot lacks Speak permission in VC | Grant **Connect** + **Speak** permissions to Yuri's role |
| `!inbox` / `!fetchlog` refuses to run | Run in a public channel | These are DM-only to prevent data leaks - DM the bot instead |
| `!wipeall` doesn't wipe | Missing `confirm` argument | Run `!wipeall confirm` (safety gate to prevent typos) |

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE.txt) file for details.

---

## 🔒 Privacy

Yuri processes message content, images, and voice notes only when explicitly mentioned or replied to. Conversational context is stored securely in MongoDB and automatically expires after 30 days. Long-term memory dossiers (anonymized summaries) persist indefinitely so Yuri can remember you. Voice TTS and image generation data is logged for 7 days for abuse prevention. Read the full [Privacy Policy](PRIVACY_POLICY.md).
