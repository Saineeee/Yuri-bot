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

if __name__ == '__main__':
    unittest.main()
