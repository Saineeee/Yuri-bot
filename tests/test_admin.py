import unittest
import sys
import os
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import datetime

# Add root directory to path to import utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock modules
sys.modules['discord'] = MagicMock()
sys.modules['discord.app_commands'] = MagicMock()

# Setup MockCog
class MockCog:
    pass

mock_ext = MagicMock()
mock_commands = MagicMock()
mock_commands.Cog = MockCog
mock_commands.Bot = MagicMock
# Mock the command decorator to return the function itself
def command_decorator(*args, **kwargs):
    def decorator(func):
        return func
    return decorator
mock_commands.command = command_decorator
mock_commands.is_owner = command_decorator

mock_ext.commands = mock_commands

sys.modules['discord.ext'] = mock_ext
sys.modules['discord.ext.commands'] = mock_commands

import cogs.admin as admin_cog

class TestAdmin(unittest.IsolatedAsyncioTestCase):
    async def test_health_command(self):
        bot = MagicMock()
        # Mock mongo
        bot.mongo = MagicMock()
        bot.mongo.admin = MagicMock()
        bot.mongo.admin.command = AsyncMock(return_value={"ok": 1})
        bot.latency = 0.05 # 50ms

        # Mock AI cog presence
        ai_cog = MagicMock()
        ai_cog.groq_client = True

        # Mock bot.get_cog
        def get_cog(name):
            if name == "AI": return ai_cog
            return None
        bot.get_cog.side_effect = get_cog

        cog = admin_cog.Admin(bot)
        ctx = MagicMock()
        ctx.send = AsyncMock()

        await cog.health(ctx)

        ctx.send.assert_called_once()
        msg = ctx.send.call_args[0][0]
        self.assertIn("✅ Connected", msg)
        self.assertIn("✅ Active", msg)

if __name__ == '__main__':
    unittest.main()
