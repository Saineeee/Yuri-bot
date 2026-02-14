import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import io
import datetime
import sys
import utils
from PIL import Image

class TestUtils(unittest.IsolatedAsyncioTestCase):

    async def test_get_image_from_url_success(self):
        # Mock aiohttp
        with patch('aiohttp.ClientSession') as MockSession:
            # The session object that acts as the interface
            mock_session_obj = MagicMock()

            # The context manager returned by calling ClientSession()
            session_cm = MagicMock()
            session_cm.__aenter__ = AsyncMock(return_value=mock_session_obj)
            session_cm.__aexit__ = AsyncMock(return_value=None)

            MockSession.return_value = session_cm

            # Create a small valid image
            img = Image.new('RGB', (10, 10), color = 'red')
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            img_bytes = img_byte_arr.getvalue()

            # Setup the response
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.headers = {'Content-Length': '100'}

            # Setup content.iter_chunked
            async def iter_chunked(chunk_size):
                yield img_bytes

            mock_resp.content = MagicMock()
            mock_resp.content.iter_chunked.side_effect = iter_chunked

            # Setup the response context manager returned by session.get()
            resp_cm = MagicMock()
            resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            resp_cm.__aexit__ = AsyncMock(return_value=None)

            mock_session_obj.get.return_value = resp_cm

            result = await utils.get_image_from_url("http://example.com/image.png")
            self.assertIsInstance(result, Image.Image)

    async def test_get_image_from_url_too_large(self):
        with patch('aiohttp.ClientSession') as MockSession:
            mock_session_obj = MagicMock()
            session_cm = MagicMock()
            session_cm.__aenter__ = AsyncMock(return_value=mock_session_obj)
            session_cm.__aexit__ = AsyncMock(return_value=None)
            MockSession.return_value = session_cm

            mock_resp = AsyncMock()
            mock_resp.status = 200
            # 9MB
            mock_resp.headers = {'Content-Length': str(9 * 1024 * 1024)}

            resp_cm = MagicMock()
            resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            resp_cm.__aexit__ = AsyncMock(return_value=None)

            mock_session_obj.get.return_value = resp_cm

            result = await utils.get_image_from_url("http://example.com/large.png")
            self.assertIsNone(result)

    async def test_get_image_from_url_failure(self):
         with patch('aiohttp.ClientSession') as MockSession:
            mock_session_obj = MagicMock()
            session_cm = MagicMock()
            session_cm.__aenter__ = AsyncMock(return_value=mock_session_obj)
            session_cm.__aexit__ = AsyncMock(return_value=None)
            MockSession.return_value = session_cm

            mock_resp = AsyncMock()
            mock_resp.status = 404

            resp_cm = MagicMock()
            resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            resp_cm.__aexit__ = AsyncMock(return_value=None)

            mock_session_obj.get.return_value = resp_cm

            result = await utils.get_image_from_url("http://example.com/missing.png")
            self.assertIsNone(result)

    def test_get_smart_time(self):
        # Input with Hindi -> IST
        res = utils.get_smart_time("kya haal hai")
        self.assertIn("(IST)", res)

        # Input with Japanese -> JST
        res = utils.get_smart_time("こんにちは")
        self.assertIn("(JST)", res)

        # Default -> IST
        res = utils.get_smart_time("Hello world")
        self.assertIn("(IST)", res)

    async def test_search_web(self):
        with patch('utils.DDGS') as MockDDGS:
            mock_ddgs_instance = MockDDGS.return_value
            mock_ddgs_instance.text.return_value = [
                {'title': 'Test Title', 'body': 'Test Snippet'}
            ]

            result = await utils.search_web("test query")
            self.assertIn("Test Title", result)
            self.assertIn("Test Snippet", result)

    async def test_search_web_no_results(self):
        with patch('utils.DDGS') as MockDDGS:
            mock_ddgs_instance = MockDDGS.return_value
            mock_ddgs_instance.text.return_value = []

            result = await utils.search_web("test query")
            self.assertIsNone(result)

    async def test_process_gif_tags(self):
        with patch('utils.search_gif_ddg', new_callable=AsyncMock) as mock_search:
            mock_search.return_value = "http://example.com/funny.gif"

            text = "Hello [GIF: funny cat]"
            clean_text, gif_url = await utils.process_gif_tags(text)

            self.assertEqual(clean_text, "Hello")
            self.assertEqual(gif_url, "http://example.com/funny.gif")
            mock_search.assert_called_with("funny cat")

    async def test_process_gif_tags_no_tag(self):
        text = "Hello world"
        clean_text, gif_url = await utils.process_gif_tags(text)
        self.assertEqual(clean_text, "Hello world")
        self.assertIsNone(gif_url)

if __name__ == '__main__':
    unittest.main()
