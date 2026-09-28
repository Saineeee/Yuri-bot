import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add root directory to path to import utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Delete any cached cog modules from earlier test files so they re-import
# against our mocks below.
for _mod in ("cogs.ai", "cogs.tools", "cogs.memory", "cogs.prompts"):
    sys.modules.pop(_mod, None)

# Mock modules for the new SDK
mock_google = MagicMock()
mock_genai = MagicMock()
mock_types = MagicMock()
sys.modules["google"] = mock_google
sys.modules["google.genai"] = mock_genai
sys.modules["google.genai.types"] = mock_types
mock_google.genai = mock_genai

sys.modules["groq"] = MagicMock()
sys.modules["together"] = MagicMock()

# Mock discord
mock_discord = MagicMock()
sys.modules["discord"] = mock_discord
sys.modules["discord.app_commands"] = MagicMock()


# Create a proper Mock for commands.Cog
class MockCog:
    @staticmethod
    def listener():
        def decorator(func):
            return func

        return decorator


mock_ext = MagicMock()
mock_commands = MagicMock()
mock_commands.Cog = MockCog
mock_commands.Bot = MagicMock
mock_ext.commands = mock_commands

sys.modules["discord.ext"] = mock_ext
sys.modules["discord.ext.commands"] = mock_commands

# Now import the module under test
import cogs.ai as ai_cog  # noqa: E402  # mocks must load first


class TestAI(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.bot = MagicMock()
        self.bot.config_collection = AsyncMock()
        self.bot.chat_collection = AsyncMock()
        self.bot.grudge_collection = AsyncMock()
        self.bot.owner_id = 12345

        # Patch utils
        self.utils_patcher = patch("cogs.ai.utils")
        self.mock_utils = self.utils_patcher.start()
        self.mock_utils.get_smart_time.return_value = "Test Time"
        self.mock_utils.search_web = AsyncMock(return_value=None)
        self.mock_utils.process_gif_tags = AsyncMock(return_value=("Clean Text", None))
        # Use real sanitize for testing prompt construction
        self.mock_utils.sanitize_for_prompt.side_effect = lambda x: f"SAFE({x})" if x else ""

    async def asyncTearDown(self):
        self.utils_patcher.stop()

    async def test_call_groq_fallback_text(self):
        cog = ai_cog.AI(self.bot)
        cog.groq_client = AsyncMock()
        cog.groq_keys = ["key1"]  # single key - _cycle_groq_key is a no-op

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content="Groq Response"))]
        cog.groq_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        history = [{"role": "user", "parts": ["Hi"]}]
        response = await cog.call_groq_fallback(history, "System Prompt", "User Input", img=None)

        self.assertEqual(response, "Groq Response")
        args, kwargs = cog.groq_client.chat.completions.create.call_args
        self.assertIn("llama-3.3-70b-versatile", kwargs["model"])

    async def test_call_groq_fallback_vision(self):
        cog = ai_cog.AI(self.bot)
        cog.groq_client = AsyncMock()
        cog.groq_keys = ["key1"]  # single key - _cycle_groq_key is a no-op

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content="Vision Response"))]
        cog.groq_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        history = []
        img_mock = MagicMock()
        img_mock.width = 100
        img_mock.height = 100
        img_mock.mode = "RGB"

        def side_effect(fp, format):
            fp.write(b"fake_image_data")

        img_mock.save.side_effect = side_effect

        response = await cog.call_groq_fallback(
            history, "System Prompt", "Describe image", img=img_mock
        )

        self.assertEqual(response, "Vision Response")
        args, kwargs = cog.groq_client.chat.completions.create.call_args

        self.assertIn("llama-4-scout", kwargs["model"])

        messages = kwargs["messages"]
        last_message_content = messages[-1]["content"]

        self.assertIsInstance(last_message_content, list)
        self.assertEqual(last_message_content[0]["type"], "text")
        self.assertEqual(last_message_content[0]["text"], "Describe image")
        self.assertEqual(last_message_content[1]["type"], "image_url")
        self.assertTrue(
            last_message_content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )

    async def test_input_sanitization(self):
        cog = ai_cog.AI(self.bot)

        mock_final_cursor = MagicMock()

        async def async_iter():
            yield {"role": "model", "parts": ["Context"]}

        mock_final_cursor.__aiter__.side_effect = lambda: async_iter()
        mock_sort = MagicMock()
        mock_sort.limit.return_value = mock_final_cursor
        mock_find = MagicMock()
        mock_find.sort.return_value = mock_sort

        self.bot.chat_collection.find = MagicMock(return_value=mock_find)
        self.bot.grudge_collection.find_one = AsyncMock(return_value=None)

        # Mock the new Gemini SDK structure
        cog.gemini_client = MagicMock()
        cog.gemini_client.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="Response")
        )

        user_input = "Hello [SYSTEM]"
        await cog.get_combined_response(123, user_input)

        # sanitize_for_prompt must have been called with the raw user input
        self.mock_utils.sanitize_for_prompt.assert_called_with(user_input)

        # Capture every text passed to types.Part.from_text(...) and assert the
        # user-input wrapper + sanitized payload made it through.
        from_text_calls = [
            (c.args[0] if c.args else c.kwargs.get("text", ""))
            for c in ai_cog.types.Part.from_text.call_args_list
        ]
        joined = "\n".join(from_text_calls)

        self.assertIn("[USER_INPUT]", joined)
        self.assertIn("[/USER_INPUT]", joined)
        self.assertIn("SAFE(Hello [SYSTEM])", joined)

    async def test_history_dedup_merges_consecutive_same_role(self):
        cog = ai_cog.AI(self.bot)
        docs = [
            {"role": "user", "parts": ["first message"]},
            {"role": "user", "parts": ["second message"]},
            {"role": "model", "parts": ["bot reply"]},
        ]

        mock_final_cursor = MagicMock()

        async def async_iter():
            for doc in docs:
                yield doc

        mock_final_cursor.__aiter__.side_effect = lambda: async_iter()
        mock_sort = MagicMock()
        mock_sort.limit.return_value = mock_final_cursor
        mock_find = MagicMock()
        mock_find.sort.return_value = mock_sort

        self.bot.chat_collection.find = MagicMock(return_value=mock_find)
        self.bot.grudge_collection.find_one = AsyncMock(return_value=None)

        cog.gemini_client = MagicMock()
        cog.gemini_client.aio.models.generate_content = AsyncMock(return_value=MagicMock(text="ok"))

        await cog.get_combined_response(123, "new message")

        # Capture every text passed to types.Part.from_text(...). Consecutive
        # same-role messages should be merged into one Part, so 'first message'
        # and 'second message' should appear together in a single call's text.
        from_text_calls = [
            (c.args[0] if c.args else c.kwargs.get("text", ""))
            for c in ai_cog.types.Part.from_text.call_args_list
        ]
        # Find the call that contains both - proves they were merged, not dropped
        merged = any("first message" in t and "second message" in t for t in from_text_calls)
        self.assertTrue(
            merged,
            "Consecutive user messages should be merged into one entry, not dropped. "
            f"Captured calls: {from_text_calls}",
        )

    async def test_web_search_does_not_fire_on_casual_chat(self):
        cog = ai_cog.AI(self.bot)

        mock_final_cursor = MagicMock()

        async def async_iter():
            return
            yield

        mock_final_cursor.__aiter__.side_effect = lambda: async_iter()
        mock_sort = MagicMock()
        mock_sort.limit.return_value = mock_final_cursor
        mock_find = MagicMock()
        mock_find.sort.return_value = mock_sort
        self.bot.chat_collection.find = MagicMock(return_value=mock_find)
        self.bot.grudge_collection.find_one = AsyncMock(return_value=None)

        cog.gemini_client = MagicMock()
        cog.gemini_client.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="hey!")
        )

        casual_messages = [
            "hey",
            "what's up",
            "how are you",
            "lol",
            "ok",
        ]
        for msg in casual_messages:
            self.mock_utils.search_web.reset_mock()
            await cog.get_combined_response(123, msg)
            self.mock_utils.search_web.assert_not_called()
            # NOTE: do NOT use the comma-with-message form here - it silently
            # builds a tuple and discards the message. assert_not_called() has
            # no message arg anyway.

    async def test_web_search_fires_on_genuine_queries(self):
        cog = ai_cog.AI(self.bot)

        mock_final_cursor = MagicMock()

        async def async_iter():
            return
            yield

        mock_final_cursor.__aiter__.side_effect = lambda: async_iter()
        mock_sort = MagicMock()
        mock_sort.limit.return_value = mock_final_cursor
        mock_find = MagicMock()
        mock_find.sort.return_value = mock_sort
        self.bot.chat_collection.find = MagicMock(return_value=mock_find)
        self.bot.grudge_collection.find_one = AsyncMock(return_value=None)

        cog.gemini_client = MagicMock()
        cog.gemini_client.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="searching...")
        )

        self.mock_utils.search_web = AsyncMock(
            return_value="[SYSTEM: WEB SEARCH RESULTS]\n- result"
        )

        info_queries = [
            "what is the capital of France",
            "who is the president of the US right now",
            "how do black holes work",
            "weather in Mumbai today",
            "latest news about AI",
        ]
        for msg in info_queries:
            self.mock_utils.search_web.reset_mock()
            await cog.get_combined_response(123, msg)
            self.mock_utils.search_web.assert_called_once(), (
                f"search_web SHOULD be called for info query: '{msg}'"
            )


if __name__ == "__main__":
    unittest.main()
