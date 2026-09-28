"""Tests for the auto-speak feature in cogs/voicetts.py + cogs/ai.py.

Verifies:
  - /voice command toggles the preference in MongoDB
  - is_voice_mode_enabled() defaults to True (on)
  - speak_in_guild_vc() truncates long text
  - _maybe_auto_speak() no-ops correctly when voice mode is off,
    when Yuri isn't in a VC, or when the VoiceTTS cog isn't loaded
"""

import asyncio  # noqa: E402  # mocks must load first
import os  # noqa: E402  # mocks must load first
import sys  # noqa: E402  # mocks must load first
import unittest  # noqa: E402  # mocks must load first
from unittest.mock import AsyncMock, MagicMock  # noqa: E402  # mocks must load first

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class MockCog:
    @classmethod
    def listener(cls):
        def decorator(func):
            return func

        return decorator

    def __init__(self, *args, **kwargs):
        pass


def command_decorator(*args, **kwargs):
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def deco(fn):
        return fn

    return deco


mock_commands = MagicMock()
mock_commands.Cog = MockCog
mock_commands.command = command_decorator
mock_commands.is_owner = command_decorator

mock_ext = MagicMock()
mock_ext.commands = mock_commands

mock_discord = MagicMock()
mock_app_commands = MagicMock()
mock_app_commands.command = command_decorator
mock_app_commands.describe = command_decorator
mock_app_commands.choices = command_decorator
mock_app_commands.checks = MagicMock()
mock_app_commands.checks.cooldown = command_decorator
mock_app_commands.checks.has_permissions = command_decorator
mock_app_commands.checks.bot_has_permissions = command_decorator
mock_app_commands.checks.is_owner = command_decorator
mock_app_commands.Choice = MagicMock()
mock_discord.app_commands = mock_app_commands

# Force-reset
sys.modules["discord"] = mock_discord
sys.modules["discord.ext"] = mock_ext
sys.modules["discord.ext.commands"] = mock_commands
sys.modules["discord.ext.tasks"] = MagicMock()
sys.modules["discord.app_commands"] = mock_app_commands

# Mock google.genai so AI cog can be instantiated without a real API key
mock_google = MagicMock()
mock_genai = MagicMock()
mock_types = MagicMock()
mock_google.genai = mock_genai
sys.modules["google"] = mock_google
sys.modules["google.genai"] = mock_genai
sys.modules["google.genai.types"] = mock_types

# Mock groq + together so AI cog __init__ doesn't fail
sys.modules["groq"] = MagicMock()
sys.modules["together"] = MagicMock()

# Delete cached cog modules so they re-import against our mocks.
# IMPORTANT: do NOT pop 'cogs.ai' here - test_ai.py needs its own version
# cached in sys.modules for patch('cogs.ai.utils') to work correctly.
# We import AI at module level below, which will use whatever version is
# already cached (test_ai.py's, if collection order puts it first) or import
# a fresh one with our mocks if it's not cached yet.
for _mod in ("cogs.voicetts", "cogs.tools", "cogs.memory", "cogs.prompts"):
    sys.modules.pop(_mod, None)

# Set dummy env vars for AI cog pre-flight
os.environ["GEMINI_API_KEY"] = "dummy"
os.environ["GROQ_API_KEY"] = "dummy"

from cogs.ai import (  # noqa: E402  # import at module level so test_ai.py can re-import
    AI,
)
from cogs.voicetts import MAX_TTS_CHARS, VoiceTTS  # noqa: E402  # mocks must load first

# with its own mocks without breaking our reference


class TestVoiceModeToggle(unittest.IsolatedAsyncioTestCase):
    """/voice on / /voice off must persist the preference to MongoDB."""

    async def test_voice_on_persists_to_db(self):
        bot = MagicMock()
        bot.config_collection = MagicMock()
        bot.config_collection.update_one = AsyncMock()
        cog = VoiceTTS(bot)

        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild_id = 123
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        mode = MagicMock()
        mode.value = "on"

        await cog.voice(interaction, mode)

        # config_collection.update_one must have been called with voice_mode_enabled=True
        bot.config_collection.update_one.assert_called_once()
        call_args = bot.config_collection.update_one.call_args
        update_doc = call_args[0][1]
        self.assertEqual(update_doc["$set"]["voice_mode_enabled"], True)

    async def test_voice_off_persists_to_db(self):
        bot = MagicMock()
        bot.config_collection = MagicMock()
        bot.config_collection.update_one = AsyncMock()
        cog = VoiceTTS(bot)

        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild_id = 123
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        mode = MagicMock()
        mode.value = "off"

        await cog.voice(interaction, mode)

        bot.config_collection.update_one.assert_called_once()
        call_args = bot.config_collection.update_one.call_args
        update_doc = call_args[0][1]
        self.assertEqual(update_doc["$set"]["voice_mode_enabled"], False)


class TestIsVoiceModeEnabled(unittest.IsolatedAsyncioTestCase):
    """is_voice_mode_enabled() must default to True (on)."""

    async def test_defaults_to_true_when_no_config(self):
        bot = MagicMock()
        bot.config_collection = AsyncMock()
        bot.config_collection.find_one = AsyncMock(return_value=None)

        cog = VoiceTTS(bot)
        result = await cog.is_voice_mode_enabled(123)
        self.assertTrue(result, "voice mode should default to ON when no config exists")

    async def test_returns_false_when_disabled(self):
        bot = MagicMock()
        bot.config_collection = AsyncMock()
        bot.config_collection.find_one = AsyncMock(
            return_value={"guild_id": 123, "voice_mode_enabled": False}
        )

        cog = VoiceTTS(bot)
        result = await cog.is_voice_mode_enabled(123)
        self.assertFalse(result)

    async def test_returns_true_when_enabled(self):
        bot = MagicMock()
        bot.config_collection = AsyncMock()
        bot.config_collection.find_one = AsyncMock(
            return_value={"guild_id": 123, "voice_mode_enabled": True}
        )

        cog = VoiceTTS(bot)
        result = await cog.is_voice_mode_enabled(123)
        self.assertTrue(result)


class TestIsInVC(unittest.IsolatedAsyncioTestCase):
    """is_in_vc() must report whether Yuri is connected to a VC."""

    async def test_returns_false_when_not_connected(self):
        bot = MagicMock()
        cog = VoiceTTS(bot)
        # No voice client in the dict
        result = await cog.is_in_vc(123)
        self.assertFalse(result)

    async def test_returns_true_when_connected(self):
        bot = MagicMock()
        cog = VoiceTTS(bot)
        vc = MagicMock()
        vc.is_connected = MagicMock(return_value=True)
        cog._voice_clients[123] = vc

        result = await cog.is_in_vc(123)
        self.assertTrue(result)

    async def test_returns_false_when_disconnected(self):
        bot = MagicMock()
        cog = VoiceTTS(bot)
        vc = MagicMock()
        vc.is_connected = MagicMock(return_value=False)
        cog._voice_clients[123] = vc

        result = await cog.is_in_vc(123)
        self.assertFalse(result)


class TestSpeakInGuildVC(unittest.IsolatedAsyncioTestCase):
    """speak_in_guild_vc() must truncate, generate TTS, and play audio."""

    async def test_returns_false_when_not_in_vc(self):
        bot = MagicMock()
        cog = VoiceTTS(bot)
        # No voice client → should return False
        result = await cog.speak_in_guild_vc(123, "hello")
        self.assertFalse(result)

    async def test_returns_false_for_empty_text(self):
        bot = MagicMock()
        cog = VoiceTTS(bot)
        vc = MagicMock()
        vc.is_connected = MagicMock(return_value=True)
        cog._voice_clients[123] = vc

        result = await cog.speak_in_guild_vc(123, "")
        self.assertFalse(result)
        result = await cog.speak_in_guild_vc(123, "   ")
        self.assertFalse(result)

    async def test_truncates_long_text(self):
        """Text longer than MAX_TTS_CHARS must be truncated before TTS."""
        bot = MagicMock()
        cog = VoiceTTS(bot)
        vc = MagicMock()
        vc.is_connected = MagicMock(return_value=True)
        vc.is_playing = MagicMock(return_value=False)
        cog._voice_clients[123] = vc

        # Mock _generate_tts to capture the text it receives
        captured_text = []

        async def mock_tts(text):
            captured_text.append(text)
            return b"fake_audio"

        cog._generate_tts = mock_tts

        long_text = "x" * (MAX_TTS_CHARS + 100)
        await cog.speak_in_guild_vc(123, long_text)

        self.assertEqual(len(captured_text), 1)
        # Truncated text must be <= MAX_TTS_CHARS
        self.assertLessEqual(len(captured_text[0]), MAX_TTS_CHARS)

    async def test_interrupts_current_playback(self):
        """If something is already playing, it must be stopped before new audio."""
        bot = MagicMock()
        cog = VoiceTTS(bot)
        vc = MagicMock()
        vc.is_connected = MagicMock(return_value=True)
        vc.is_playing = MagicMock(return_value=True)  # currently playing
        cog._voice_clients[123] = vc

        async def mock_tts(text):
            return b"fake_audio"

        cog._generate_tts = mock_tts

        await cog.speak_in_guild_vc(123, "hello")

        vc.stop.assert_called_once()
        vc.play.assert_called_once()

    async def test_returns_true_on_success(self):
        bot = MagicMock()
        cog = VoiceTTS(bot)
        vc = MagicMock()
        vc.is_connected = MagicMock(return_value=True)
        vc.is_playing = MagicMock(return_value=False)
        cog._voice_clients[123] = vc

        async def mock_tts(text):
            return b"fake_audio"

        cog._generate_tts = mock_tts

        result = await cog.speak_in_guild_vc(123, "hello")
        self.assertTrue(result)


class TestMaybeAutoSpeak(unittest.IsolatedAsyncioTestCase):
    """AI cog's _maybe_auto_speak() must no-op correctly in all edge cases."""

    async def test_noops_when_voicetts_cog_not_loaded(self):
        """If the VoiceTTS cog isn't loaded, auto-speak must silently no-op."""
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=None)  # VoiceTTS not loaded

        cog = AI(bot)
        # Should not raise
        await cog._maybe_auto_speak(123, "hello")

    async def test_noops_when_voice_mode_disabled(self):
        bot = MagicMock()
        voice_cog = MagicMock()
        voice_cog.is_voice_mode_enabled = AsyncMock(return_value=False)
        voice_cog.is_in_vc = AsyncMock(return_value=True)
        voice_cog.speak_in_guild_vc = AsyncMock()
        bot.get_cog = MagicMock(return_value=voice_cog)

        cog = AI(bot)
        await cog._maybe_auto_speak(123, "hello")

        # speak_in_guild_vc must NOT have been called (mode is off)
        voice_cog.speak_in_guild_vc.assert_not_called()

    async def test_noops_when_not_in_vc(self):
        bot = MagicMock()
        voice_cog = MagicMock()
        voice_cog.is_voice_mode_enabled = AsyncMock(return_value=True)
        voice_cog.is_in_vc = AsyncMock(return_value=False)  # not in VC
        voice_cog.speak_in_guild_vc = AsyncMock()
        bot.get_cog = MagicMock(return_value=voice_cog)

        cog = AI(bot)
        await cog._maybe_auto_speak(123, "hello")

        voice_cog.speak_in_guild_vc.assert_not_called()

    async def test_noops_for_empty_text(self):
        bot = MagicMock()
        voice_cog = MagicMock()
        voice_cog.is_voice_mode_enabled = AsyncMock(return_value=True)
        voice_cog.is_in_vc = AsyncMock(return_value=True)
        voice_cog.speak_in_guild_vc = AsyncMock()
        bot.get_cog = MagicMock(return_value=voice_cog)

        cog = AI(bot)
        await cog._maybe_auto_speak(123, "")
        await cog._maybe_auto_speak(123, "   ")

        voice_cog.speak_in_guild_vc.assert_not_called()

    async def test_calls_speak_when_all_conditions_met(self):
        """When mode is on + in VC + has text, speak_in_guild_vc must be called."""
        bot = MagicMock()
        voice_cog = MagicMock()
        voice_cog.is_voice_mode_enabled = AsyncMock(return_value=True)
        voice_cog.is_in_vc = AsyncMock(return_value=True)
        voice_cog.speak_in_guild_vc = AsyncMock(return_value=True)
        bot.get_cog = MagicMock(return_value=voice_cog)
        # bot.loop.create_task must actually schedule the coroutine
        bot.loop = MagicMock()
        bot.loop.create_task = MagicMock(side_effect=lambda coro: asyncio.ensure_future(coro))

        cog = AI(bot)
        await cog._maybe_auto_speak(123, "hello there")

        # create_task must have been called to schedule the speak
        bot.loop.create_task.assert_called_once()


class TestVoiceCommandSourceAssertions(unittest.TestCase):
    """Source-level assertions that the /voice command exists and is wired up."""

    @staticmethod
    def _read(path):
        full = os.path.join(os.path.dirname(__file__), "..", path)
        with open(full, encoding="utf-8") as f:
            return f.read()

    def test_voice_command_exists_in_voicetts(self):
        src = self._read("cogs/voicetts.py")
        self.assertIn('name="voice"', src)
        self.assertIn("is_voice_mode_enabled", src)
        self.assertIn("speak_in_guild_vc", src)
        self.assertIn("is_in_vc", src)

    def test_auto_speak_hook_in_ai_cog(self):
        src = self._read("cogs/ai.py")
        self.assertIn("_maybe_auto_speak", src)
        self.assertIn("VoiceTTS", src)

    def test_help_lists_voice_command(self):
        src = self._read("cogs/general.py")
        self.assertIn("/voice", src)


if __name__ == "__main__":
    unittest.main()
