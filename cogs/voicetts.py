"""Voice TTS: /say speaks a message in the caller's VC, /vc joins/leaves,
and /voice toggles auto-speak for @mention replies. Groq TTS with gTTS
fallback; degrades gracefully if PyNaCl/ffmpeg/gTTS are missing."""

import asyncio
import contextlib
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

import utils

log = logging.getLogger(__name__)


# TTS libs are optional, degrade gracefully if missing
try:
    from gtts import gTTS

    _GTTS_AVAILABLE = True
except ImportError:
    _GTTS_AVAILABLE = False
    log.info("gTTS not installed, TTS will use Groq only (if available).")


MAX_TTS_CHARS = 500
SAY_COOLDOWN_SECS = 10


class VoiceTTS(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._voice_clients: dict[int, discord.VoiceClient] = {}  # guild_id -> client

    @app_commands.command(
        name="say",
        description="Make Yuri say something out loud in your voice channel.",
    )
    @app_commands.describe(text="What should Yuri say? (max 500 chars)")
    @app_commands.checks.cooldown(1, SAY_COOLDOWN_SECS)
    async def say(self, interaction: discord.Interaction, text: str) -> None:
        """Generate TTS audio and play it in the caller's voice channel."""
        if not interaction.guild:
            await interaction.response.send_message(
                "this only works in a server 💀", ephemeral=True
            )
            return

        if len(text) > MAX_TTS_CHARS:
            await interaction.response.send_message(
                f"keep it under {MAX_TTS_CHARS} chars bestie 💀",
                ephemeral=True,
            )
            return

        # Caller must be in a voice channel
        voice_state = interaction.user.voice
        if voice_state is None or voice_state.channel is None:
            await interaction.response.send_message(
                "you need to be in a voice channel first 💀",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        # join (or move to) the caller's channel
        voice_client = await self._join_or_move(voice_state.channel)
        if voice_client is None:
            await interaction.followup.send(
                "couldn't join the voice channel 💀 check my permissions " "(Connect + Speak)"
            )
            return

        # generate TTS, then play it (interrupting anything already playing)
        safe_text = utils.sanitize_for_prompt(text)
        audio_bytes = await self._generate_tts(safe_text)
        if audio_bytes is None:
            await interaction.followup.send("couldn't generate audio rn 💀 try again")
            return

        if voice_client.is_playing():
            voice_client.stop()

        audio_source = discord.FFmpegPCMAudio(io.BytesIO(audio_bytes), pipe=True)
        voice_client.play(audio_source)

        public_text = utils.sanitize_for_discord(text)
        embed = discord.Embed(
            title="🔊 Yuri says",
            description=public_text,
            color=discord.Color.from_rgb(255, 105, 180),
            timestamp=utils.utcnow(),
        )
        embed.set_footer(text=f"in {voice_state.channel.name} • /say")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="vc",
        description="Make Yuri join or leave your voice channel.",
    )
    @app_commands.describe(action="join or leave")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="join", value="join"),
            app_commands.Choice(name="leave", value="leave"),
        ]
    )
    async def vc(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ) -> None:
        """Join or leave a voice channel."""
        if not interaction.guild:
            await interaction.response.send_message(
                "this only works in a server 💀", ephemeral=True
            )
            return

        if action.value == "join":
            voice_state = interaction.user.voice
            if voice_state is None or voice_state.channel is None:
                await interaction.response.send_message(
                    "you need to be in a voice channel first 💀",
                    ephemeral=True,
                )
                return

            voice_client = await self._join_or_move(voice_state.channel)
            if voice_client is None:
                await interaction.response.send_message(
                    "couldn't join 💀 check my permissions",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                f"✅ joined {voice_state.channel.mention}! use `/say` to make me talk"
            )

        elif action.value == "leave":
            voice_client = self._voice_clients.get(interaction.guild_id)
            if voice_client is None or not voice_client.is_connected():
                await interaction.response.send_message(
                    "i'm not in a voice channel 💀", ephemeral=True
                )
                return

            with contextlib.suppress(Exception):
                voice_client.stop()
            await voice_client.disconnect()
            self._voice_clients.pop(interaction.guild_id, None)
            await interaction.response.send_message("👋 left the voice channel")

    @app_commands.command(
        name="voice",
        description="Toggle auto-speak: when on, Yuri speaks her replies in the VC.",
    )
    @app_commands.describe(
        mode="on = auto-speak replies in VC, off = text-only replies, status = check current"
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
            app_commands.Choice(name="status", value="status"),
        ]
    )
    async def voice(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        """Toggle auto-speak: on = @mention replies get spoken in the VC too."""
        if not interaction.guild:
            await interaction.response.send_message(
                "this only works in a server 💀", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if mode.value == "status":
            enabled = await self.is_voice_mode_enabled(interaction.guild_id)
            in_vc = (
                interaction.guild_id in self._voice_clients
                and self._voice_clients[interaction.guild_id].is_connected()
            )
            vc_name = ""
            if in_vc:
                vc = self._voice_clients[interaction.guild_id]
                vc_name = f" (currently in **{vc.channel.name}**)" if vc.channel else ""

            status = "🟢 ON" if enabled else "🔴 OFF"
            vc_status = "✅ in a VC" if in_vc else "❌ not in a VC"

            embed = discord.Embed(
                title="🔊 Voice Mode Status",
                description=(
                    f"**Auto-speak:** {status}{vc_name}\n"
                    f"**In voice channel:** {vc_status}\n\n"
                    + (
                        "auto-speak is active, just @mention me and I'll speak my reply!"
                        if enabled and in_vc
                        else "use `/voice on` to enable auto-speak, then `/vc join` to summon me."
                    )
                ),
                color=discord.Color.from_rgb(255, 105, 180),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        enabled = mode.value == "on"
        await self.bot.config_collection.update_one(
            {"guild_id": interaction.guild_id},
            {"$set": {"voice_mode_enabled": enabled}},
            upsert=True,
        )

        if enabled:
            in_vc = (
                interaction.guild_id in self._voice_clients
                and self._voice_clients[interaction.guild_id].is_connected()
            )
            hint = (
                "\n\ni'm already in a VC, just @mention me and i'll speak!"
                if in_vc
                else "\n\nnow run `/vc join` to summon me into a voice channel."
            )
            await interaction.followup.send(
                f"✅ **auto-speak ON.** when i'm in a VC, my replies to your "
                f"@mentions will be spoken out loud.{hint}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "🔴 **auto-speak OFF.** i'll only speak when you use `/say`.",
                ephemeral=True,
            )

    async def is_voice_mode_enabled(self, guild_id: int) -> bool:
        """Check if auto-speak is enabled for this guild. Defaults to True."""
        config = await self.bot.config_collection.find_one({"guild_id": guild_id})
        if config is None:
            return True  # default: on
        return bool(config.get("voice_mode_enabled", True))

    async def is_in_vc(self, guild_id: int) -> bool:
        vc = self._voice_clients.get(guild_id)
        return vc is not None and vc.is_connected()

    async def speak_in_guild_vc(self, guild_id: int, text: str) -> bool:
        """Speak *text* in the guild's VC. Returns False if not connected/failing.

        Called by the AI cog after a text reply when auto-speak is on.
        """
        if not text or not text.strip():
            return False

        voice_client = self._voice_clients.get(guild_id)
        if voice_client is None or not voice_client.is_connected():
            return False

        safe_text = utils.sanitize_for_prompt(text)
        if len(safe_text) > MAX_TTS_CHARS:
            safe_text = safe_text[: MAX_TTS_CHARS - 3] + "..."

        audio_bytes = await self._generate_tts(safe_text)
        if audio_bytes is None:
            log.warning("auto-speak: TTS generation failed for guild %s", guild_id)
            return False

        if voice_client.is_playing():
            voice_client.stop()

        try:
            audio_source = discord.FFmpegPCMAudio(io.BytesIO(audio_bytes), pipe=True)
            voice_client.play(audio_source)
            return True
        except Exception as e:
            log.warning("auto-speak: playback failed in guild %s: %s", guild_id, e)
            return False

    async def _join_or_move(self, channel: discord.VoiceChannel) -> discord.VoiceClient | None:
        """Join *channel*, or move to it if already connected elsewhere in the guild."""
        guild_id = channel.guild.id
        existing = self._voice_clients.get(guild_id)

        if existing is not None and existing.is_connected():
            if existing.channel.id == channel.id:
                return existing
            try:
                await existing.move_to(channel)
                return existing
            except Exception as e:
                log.warning("failed to move to channel %s: %s", channel.id, e)
                return None

        try:
            voice_client = await channel.connect(timeout=15.0)
            self._voice_clients[guild_id] = voice_client
            return voice_client
        except Exception as e:
            log.warning("failed to join channel %s: %s", channel.id, e)
            return None

    async def _generate_tts(self, text: str) -> bytes | None:
        """TTS bytes for FFmpegPCMAudio, or None. Groq first, gTTS fallback."""
        ai_cog = self.bot.get_cog("AI")
        if ai_cog is not None and ai_cog.groq_client is not None:
            try:
                speech = await ai_cog.groq_client.audio.speech.create(
                    model="playai-tts",
                    voice="Bunny-PlayAI",  # friendly female voice
                    input=text,
                    response_format="wav",
                )
                # the response can be raw bytes, a file-like, or a response object
                audio_bytes = speech.read() if hasattr(speech, "read") else speech
                if isinstance(audio_bytes, (bytes, bytearray)):
                    return bytes(audio_bytes)
                if hasattr(audio_bytes, "content"):
                    return audio_bytes.content
            except Exception as e:
                log.warning("Groq TTS failed, falling back to gTTS: %s", e)

        # gTTS fallback (MP3, FFmpeg handles the conversion)
        if not _GTTS_AVAILABLE:
            log.warning("No TTS backend available (Groq failed + gTTS not installed)")
            return None

        try:
            tts = await asyncio.to_thread(gTTS, text=text, lang="en", slow=False)
            buf = io.BytesIO()
            await asyncio.to_thread(tts.write_to_fp, buf)
            buf.seek(0)
            return buf.read()
        except Exception as e:
            log.error("gTTS failed: %s", e)
            return None

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Auto-disconnect if Yuri is left alone in a voice channel."""
        if member.guild is None:
            return
        voice_client = self._voice_clients.get(member.guild.id)
        if voice_client is None or not voice_client.is_connected():
            return

        vc_channel = voice_client.channel
        if vc_channel is None:
            return

        humans = [m for m in vc_channel.members if not m.bot]
        if len(humans) == 0:
            with contextlib.suppress(Exception):
                voice_client.stop()
            await voice_client.disconnect()
            self._voice_clients.pop(member.guild.id, None)
            log.info(
                "auto-disconnected from empty VC %s in guild %s", vc_channel.id, member.guild.id
            )


async def setup(bot: commands.Bot) -> None:
    # voice needs PyNaCl, skip the cog entirely if it's missing
    try:
        import nacl  # noqa: F401
    except ImportError:
        log.warning(
            "PyNaCl not installed, VoiceTTS cog disabled. " "Install with: pip install PyNaCl"
        )
        return
    await bot.add_cog(VoiceTTS(bot))
