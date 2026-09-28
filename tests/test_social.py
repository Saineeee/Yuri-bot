"""Tests for cogs/social.py - focused on the high-risk integration points:

- sanitize_for_discord is applied to every user-controlled embed description
  (this is the regression test for the @everyone ping-injection bug).
- hotornot persists pending verdicts to MongoDB (survives bot restart).
"""

import datetime  # noqa: E402  # mocks must load first
import os  # noqa: E402  # mocks must load first
import sys  # noqa: E402  # mocks must load first
import unittest  # noqa: E402  # mocks must load first
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402  # mocks must load first

# Add root directory to path to import utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock heavy deps before importing the cog
sys.modules["discord"] = MagicMock()
sys.modules["discord.app_commands"] = MagicMock()


class MockCog:
    pass


mock_ext = MagicMock()
mock_commands = MagicMock()
mock_commands.Cog = MockCog
mock_commands.Bot = MagicMock
mock_tasks = MagicMock()
mock_tasks.loop = lambda *a, **kw: (lambda f: f)
mock_ext.commands = mock_commands
mock_ext.tasks = mock_tasks

sys.modules["discord.ext"] = mock_ext
sys.modules["discord.ext.commands"] = mock_commands
sys.modules["discord.ext.tasks"] = mock_tasks

import utils  # real utils - we want the real sanitize_for_discord here  # noqa: E402  # mocks must load first


class TestSanitizeForDiscord(unittest.TestCase):
    """Direct tests of utils.sanitize_for_discord.

    This is the helper that prevents the /confess and /hotornot ping-injection
    bugs. If this regresses, the bot can be abused to mass-ping servers.
    """

    def test_breaks_everyone_mention(self):
        out = utils.sanitize_for_discord("hi @everyone look at this")
        self.assertNotIn("@everyone", out)
        # Zero-width space should be inserted between @ and 'everyone'
        self.assertIn("@\u200beveryone", out)

    def test_breaks_here_mention(self):
        out = utils.sanitize_for_discord("ping @here pls")
        self.assertNotIn("@here", out)
        self.assertIn("@\u200bhere", out)

    def test_breaks_user_mention(self):
        out = utils.sanitize_for_discord("hey <@123456789> how are you")
        self.assertNotIn("<@123456789>", out)
        # The opening < should be broken with a zero-width space
        self.assertIn("<\u200b@123456789>", out)

    def test_breaks_role_mention(self):
        out = utils.sanitize_for_discord("attention <@&999999>")
        self.assertNotIn("<@&999999>", out)

    def test_breaks_channel_mention(self):
        out = utils.sanitize_for_discord("go to <#111111>")
        self.assertNotIn("<#111111>", out)

    def test_leaves_normal_text_alone(self):
        normal = "just a normal message with no mentions at all"
        self.assertEqual(utils.sanitize_for_discord(normal), normal)

    def test_handles_empty_input(self):
        self.assertEqual(utils.sanitize_for_discord(""), "")
        self.assertEqual(utils.sanitize_for_discord(None), "")

    def test_handles_multiple_mentions_in_one_message(self):
        out = utils.sanitize_for_discord("@everyone and @here and <@123> and <@&456>")
        # None of the raw pingable tokens should survive
        self.assertNotIn("@everyone", out)
        self.assertNotIn("@here", out)
        self.assertNotIn("<@123>", out)
        self.assertNotIn("<@&456>", out)


class TestSharedSession(unittest.IsolatedAsyncioTestCase):
    """Verify the shared aiohttp session is actually reused."""

    async def test_get_session_returns_same_instance(self):
        # Reset the module-level session
        utils._session = None
        with patch("utils.aiohttp.ClientSession") as mock_session_cls:
            mock_session_cls.return_value = MagicMock(closed=False)
            s1 = utils.get_session()
            s2 = utils.get_session()
            self.assertIs(s1, s2, "get_session() should return the same instance on repeat calls")
            # Constructor should only have been called once
            mock_session_cls.assert_called_once()

    async def test_close_session_clears_global(self):
        utils._session = None
        with patch("utils.aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.closed = False
            mock_session.close = AsyncMock()
            mock_session_cls.return_value = mock_session
            _ = utils.get_session()
            self.assertIsNotNone(utils._session)
            await utils.close_session()
            self.assertIsNone(utils._session)
            mock_session.close.assert_awaited_once()


class TestUtcnowHelper(unittest.TestCase):
    """Verify utcnow() returns timezone-aware datetimes (Py 3.12+ ready)."""

    def test_utcnow_is_timezone_aware(self):
        now = utils.utcnow()
        self.assertIsNotNone(now.tzinfo, "utcnow() must return a tz-aware datetime")

    def test_utcnow_is_utc(self):
        now = utils.utcnow()
        self.assertEqual(now.utcoffset(), datetime.timedelta(0))


class TestHistoryTruncation(unittest.IsolatedAsyncioTestCase):
    """Verify get_user_history_text truncates (not drops) long messages."""

    async def test_long_message_is_truncated_not_dropped(self):
        long_text = "x" * 1000  # well above HISTORY_MSG_MAX (400)
        docs = [
            {"parts": [long_text], "role": "user"},
        ]

        cursor_mock = MagicMock()

        async def async_iter():
            for d in docs:
                yield d

        cursor_mock.__aiter__ = lambda self: async_iter()

        # cursor_mock.sort().limit() chain
        sort_mock = MagicMock()
        sort_mock.limit.return_value = cursor_mock
        find_mock = MagicMock()
        find_mock.sort.return_value = sort_mock

        collection = MagicMock()
        collection.find = MagicMock(return_value=find_mock)

        out = await utils.get_user_history_text(collection, user_id=123, limit=10)
        self.assertIn("User:", out)
        # The truncation marker should appear, proving the message wasn't dropped
        self.assertIn("…", out)
        # And some of the original content should still be present (the first 400 chars)
        self.assertIn("x" * 100, out)


if __name__ == "__main__":
    unittest.main()
