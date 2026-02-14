import unittest
from unittest.mock import patch, MagicMock, AsyncMock, ANY
import discord
from discord.ext import commands
import sys
import os

# Set environment variables before importing cogs.ai
os.environ["GEMINI_API_KEY"] = "fake_key"
os.environ["GROQ_API_KEY"] = "fake_groq_key"
os.environ["OWNER_ID"] = "12345"

# Mock Heavy dependencies
mock_genai = MagicMock()
sys.modules["google"] = MagicMock()
sys.modules["google.generativeai"] = mock_genai
sys.modules["google.generativeai.types"] = MagicMock()

sys.modules["groq"] = MagicMock()

import cogs.ai as ai_cog
import utils

class TestAI(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = MagicMock(spec=commands.Bot)

        # chat_collection needs to handle synchronous find() and asynchronous insert_one()
        self.bot.chat_collection = MagicMock()
        self.bot.chat_collection.insert_one = AsyncMock()

        # grudge_collection needs asynchronous find_one()
        self.bot.grudge_collection = MagicMock()
        self.bot.grudge_collection.find_one = AsyncMock(return_value=None)

        self.bot.owner_id = 12345
        self.bot.user = MagicMock(spec=discord.ClientUser)
        self.bot.user.id = 999
        self.bot.command_prefix = "!"

        # Chat history cursor mock
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor

        # Make the cursor async iterable
        # MagicMock's __aiter__ expects a standard iterable as return_value,
        # which it then iterates over asynchronously.
        mock_cursor.__aiter__.return_value = [
            {"role": "user", "parts": ["hello"]},
            {"role": "model", "parts": ["hi"]}
        ]

        self.bot.chat_collection.find.return_value = mock_cursor

        self.ai = ai_cog.AI(self.bot)

        # Mock models explicitly
        self.ai.model_1 = MagicMock()
        self.ai.model_1.generate_content_async = AsyncMock()
        self.ai.model_2 = MagicMock()
        self.ai.model_2.generate_content_async = AsyncMock()

        # Mock utils
        self.original_get_smart_time = utils.get_smart_time
        self.original_search_web = utils.search_web
        self.original_process_gif_tags = utils.process_gif_tags
        self.original_send_chunked_reply = utils.send_chunked_reply
        self.original_get_image_from_url = utils.get_image_from_url

        utils.get_smart_time = MagicMock(return_value="12:00 PM (IST)")
        utils.search_web = AsyncMock(return_value=None)
        utils.process_gif_tags = AsyncMock(return_value=("Clean response", None))
        utils.send_chunked_reply = AsyncMock()
        utils.get_image_from_url = AsyncMock(return_value=None)

    def tearDown(self):
        utils.get_smart_time = self.original_get_smart_time
        utils.search_web = self.original_search_web
        utils.process_gif_tags = self.original_process_gif_tags
        utils.send_chunked_reply = self.original_send_chunked_reply
        utils.get_image_from_url = self.original_get_image_from_url

    async def test_get_combined_response_gemini_success(self):
        # Setup successful response from model 1
        mock_response = MagicMock()
        mock_response.text = "Hello there"
        self.ai.model_1.generate_content_async.return_value = mock_response

        utils.process_gif_tags.return_value = ("Hello there", None)

        response_text, gif_url = await self.ai.get_combined_response(user_id=123, text_input="Hi")

        self.assertEqual(response_text, "Hello there")
        self.assertIsNone(gif_url)

        # Check if chat history was fetched
        self.bot.chat_collection.find.assert_called_with({"user_id": 123})

        # Check if model was called
        self.ai.model_1.generate_content_async.assert_called()

        # Check if response was saved to DB
        self.assertEqual(self.bot.chat_collection.insert_one.call_count, 2)

    async def test_get_combined_response_gemini_fail_fallback_groq(self):
        # Fail both Gemini models
        self.ai.model_1.generate_content_async.side_effect = Exception("Gemini 1 Fail")
        self.ai.model_2.generate_content_async.side_effect = Exception("Gemini 2 Fail")

        # Mock Groq fallback
        self.ai.call_groq_fallback = AsyncMock(return_value="Groq response")
        utils.process_gif_tags.return_value = ("Groq response", None)

        response_text, gif_url = await self.ai.get_combined_response(user_id=123, text_input="Hi")

        self.assertEqual(response_text, "Groq response")
        self.ai.call_groq_fallback.assert_called()

    async def test_on_message_ignores_bot(self):
        message = MagicMock(spec=discord.Message)
        message.author = self.bot.user

        await self.ai.on_message(message)

        # Ensure nothing happened (no typing context)
        message.channel.typing.assert_not_called()

    async def test_on_message_mentions_bot(self):
        message = MagicMock(spec=discord.Message)
        message.author.id = 123
        message.content = f"<@{self.bot.user.id}> Hello"
        message.attachments = []
        message.reference = None

        # Mock bot.user.mentioned_in
        self.bot.user.mentioned_in.return_value = True

        # Mock get_combined_response
        with patch.object(self.ai, 'get_combined_response', new_callable=AsyncMock) as mock_get_response:
            mock_get_response.return_value = ("Hi user", None)

            # Mock typing context manager
            typing_cm = MagicMock()
            typing_cm.__aenter__ = AsyncMock(return_value=None)
            typing_cm.__aexit__ = AsyncMock(return_value=None)
            message.channel.typing.return_value = typing_cm

            await self.ai.on_message(message)

            mock_get_response.assert_called()
            utils.send_chunked_reply.assert_called_with(message, "Hi user", mention_user=True)

if __name__ == '__main__':
    unittest.main()
