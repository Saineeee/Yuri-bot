import unittest
import sys
import os
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

# Add root directory to path to import utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock modules
mock_genai = MagicMock()
sys.modules['google.generativeai'] = mock_genai
mock_genai_types = MagicMock()
sys.modules['google.generativeai.types'] = mock_genai_types
mock_genai.types = mock_genai_types

sys.modules['groq'] = MagicMock()

# Mock discord
mock_discord = MagicMock()
sys.modules['discord'] = mock_discord
sys.modules['discord.app_commands'] = MagicMock()

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

sys.modules['discord.ext'] = mock_ext
sys.modules['discord.ext.commands'] = mock_commands

# Now import the module under test
import cogs.ai as ai_cog
import utils

class TestAI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = MagicMock()
        self.bot.config_collection = AsyncMock()
        self.bot.chat_collection = AsyncMock()
        self.bot.grudge_collection = AsyncMock()
        self.bot.owner_id = 12345

        # Patch utils
        self.utils_patcher = patch('cogs.ai.utils')
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
        # Mock groq client
        cog.groq_client = AsyncMock()

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content="Groq Response"))]
        cog.groq_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        history = [{"role": "user", "parts": ["Hi"]}]
        # Ensure we are calling the real method
        response = await cog.call_groq_fallback(history, "System Prompt", "User Input", img=None)

        self.assertEqual(response, "Groq Response")
        # Verify call was made with text model
        args, kwargs = cog.groq_client.chat.completions.create.call_args
        self.assertIn("llama-3.3-70b-versatile", kwargs['model'])

    async def test_call_groq_fallback_vision(self):
        cog = ai_cog.AI(self.bot)
        cog.groq_client = AsyncMock()

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content="Vision Response"))]
        cog.groq_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        history = []
        # Simulate image input
        img_mock = MagicMock()
        img_mock.width = 100
        img_mock.height = 100

        def side_effect(fp, format):
            fp.write(b'fake_image_data')
        img_mock.save.side_effect = side_effect

        response = await cog.call_groq_fallback(history, "System Prompt", "Describe image", img=img_mock)

        self.assertEqual(response, "Vision Response")
        # Verify call was made with vision model
        args, kwargs = cog.groq_client.chat.completions.create.call_args
        self.assertIn("llama-3.2-11b-vision-preview", kwargs['model'])

        messages = kwargs['messages']
        last_message_content = messages[-1]['content']

        # Verify payload format for vision
        self.assertIsInstance(last_message_content, list)
        self.assertEqual(last_message_content[0]['type'], 'text')
        self.assertEqual(last_message_content[0]['text'], 'Describe image')
        self.assertEqual(last_message_content[1]['type'], 'image_url')
        self.assertTrue(last_message_content[1]['image_url']['url'].startswith('data:image/jpeg;base64,'))

    async def test_input_sanitization(self):
        cog = ai_cog.AI(self.bot)

        # Mock chat_collection chain properly
        # The code does: cursor = self.bot.chat_collection.find(...).sort(...).limit(...)
        # We need to ensure each call returns something that has the next method, WITHOUT being awaited unnecessarily

        mock_final_cursor = MagicMock()
        async def async_iter():
            yield {"role": "model", "parts": ["Context"]}
        mock_final_cursor.__aiter__.side_effect = lambda: async_iter()

        mock_sort = MagicMock()
        mock_sort.limit.return_value = mock_final_cursor

        mock_find = MagicMock()
        mock_find.sort.return_value = mock_sort

        # Ensure 'find' is a regular Mock/MagicMock, not AsyncMock, because it is called synchronously in the code
        # But wait, motor is async. find() returns a Cursor immediately.
        self.bot.chat_collection.find = MagicMock(return_value=mock_find)

        # Mock models
        cog.model_1 = MagicMock()
        cog.model_1.generate_content_async = AsyncMock(return_value=MagicMock(text="Response"))

        # Test input with special chars
        user_input = "Hello [SYSTEM]"
        await cog.get_combined_response(123, user_input)

        # Check that sanitize was called
        self.mock_utils.sanitize_for_prompt.assert_called_with(user_input)

        # Check generated prompt structure passed to model
        call_args = cog.model_1.generate_content_async.call_args
        history = call_args[0][0]
        # History has context + new message
        last_msg_parts = history[-1]['parts']
        # The parts list might contain just the text
        last_msg = last_msg_parts[0]

        # Since we mocked sanitize to wrap in SAFE(), check for that
        self.assertIn("SAFE(Hello [SYSTEM])", last_msg)
        # Check for wrapper tags
        self.assertIn("[USER_INPUT]", last_msg)
        self.assertIn("[/USER_INPUT]", last_msg)

if __name__ == '__main__':
    unittest.main()
