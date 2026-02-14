import unittest
import sys
import os
import io
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from PIL import Image

# Add root directory to path to import utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import utils

class TestUtils(unittest.IsolatedAsyncioTestCase):

    async def test_get_smart_time(self):
        # Test Hindi detection
        hindi_text = "kya haal hai"
        time_str = utils.get_smart_time(hindi_text)
        self.assertIn("(IST)", time_str)

        # Test Japanese detection
        japanese_text = "こんにちは"
        time_str = utils.get_smart_time(japanese_text)
        self.assertIn("(JST)", time_str)

        # Test Default (IST)
        default_text = "Hello world"
        time_str = utils.get_smart_time(default_text)
        self.assertIn("(IST)", time_str)

    @patch('aiohttp.ClientSession.get')
    async def test_get_image_from_url_success(self, mock_get):
        # Create a valid image in memory
        img = Image.new('RGB', (100, 100), color='red')
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        img_data = img_byte_arr.read()

        # Mock response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {'Content-Length': str(len(img_data))}

        # Mock iter_chunked to return data in one chunk
        async def iter_chunked(n):
            yield img_data
        mock_resp.content.iter_chunked = iter_chunked

        # Setup context manager mock
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_resp
        mock_ctx.__aexit__.return_value = None
        mock_get.return_value = mock_ctx

        result = await utils.get_image_from_url("http://example.com/image.jpg")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, Image.Image)

    @patch('aiohttp.ClientSession.get')
    async def test_get_image_from_url_too_large_header(self, mock_get):
        # Mock response with large content length
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {'Content-Length': str(9 * 1024 * 1024)} # 9MB

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_resp
        mock_ctx.__aexit__.return_value = None
        mock_get.return_value = mock_ctx

        result = await utils.get_image_from_url("http://example.com/large.jpg")
        self.assertIsNone(result)

    @patch('aiohttp.ClientSession.get')
    async def test_get_image_from_url_too_large_stream(self, mock_get):
        # Mock response with valid header but large stream
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {} # No content length header

        # Mock iter_chunked to return 9MB data
        async def iter_chunked(n):
            yield b'a' * (9 * 1024 * 1024)
        mock_resp.content.iter_chunked = iter_chunked

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_resp
        mock_ctx.__aexit__.return_value = None
        mock_get.return_value = mock_ctx

        result = await utils.get_image_from_url("http://example.com/stream.jpg")
        self.assertIsNone(result)

    def test_stitch_images(self):
        img1 = Image.new('RGB', (100, 200), color='red')
        img2 = Image.new('RGB', (100, 200), color='blue')

        result = utils.stitch_images(img1, img2)

        self.assertIsNotNone(result)
        # Check height is standardized to 512
        self.assertEqual(result.height, 512)
        # Check width is roughly double (since aspect ratio is preserved)
        # 100/200 = 0.5 ratio. New height 512 -> width 256. Total width ~ 512.
        self.assertTrue(500 <= result.width <= 520)

if __name__ == '__main__':
    unittest.main()
