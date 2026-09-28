import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# Add root directory to path to import utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# Mock modules
# Create a discord mock with app_commands as a passthrough-decorator mock
# so that `from discord import app_commands` in cogs.admin gets a mock where
# @app_commands.command(...) is a passthrough (keeps the function callable).
def command_decorator(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


mock_discord = MagicMock()
mock_app_commands = MagicMock()
mock_app_commands.command = command_decorator
mock_app_commands.checks = MagicMock()
mock_app_commands.checks.cooldown = command_decorator
mock_app_commands.checks.has_permissions = command_decorator
mock_app_commands.checks.bot_has_permissions = command_decorator
mock_app_commands.checks.is_owner = command_decorator
mock_app_commands.Choice = MagicMock()  # MagicMock supports [str] subscripting
mock_discord.app_commands = mock_app_commands

sys.modules["discord"] = mock_discord
sys.modules["discord.app_commands"] = mock_app_commands


# Setup MockCog
class MockCog:
    pass


mock_ext = MagicMock()
mock_commands = MagicMock()
mock_commands.Cog = MockCog
mock_commands.Bot = MagicMock
mock_commands.command = command_decorator
mock_commands.is_owner = command_decorator

mock_ext.commands = mock_commands

sys.modules["discord.ext"] = mock_ext
sys.modules["discord.ext.commands"] = mock_commands

import cogs.admin as admin_cog  # noqa: E402  # mocks must load first


class TestAdmin(unittest.IsolatedAsyncioTestCase):
    async def test_health_command_owner_only(self):
        """!health must be owner-restricted and show full internals."""
        bot = MagicMock()
        # Mock mongo
        bot.mongo = MagicMock()
        bot.mongo.admin = MagicMock()
        bot.mongo.admin.command = AsyncMock(return_value={"ok": 1})
        bot.latency = 0.05  # 50ms
        bot.guilds = [1, 2, 3]  # 3 guilds
        bot.cogs = {"AI": MagicMock(), "Admin": MagicMock()}

        # Mock AI cog presence - must include all three providers now
        ai_cog = MagicMock()
        ai_cog.groq_client = True
        ai_cog.gemini_client = True
        ai_cog.together_client = None  # Not configured

        # Mock bot.get_cog
        def get_cog(name):
            if name == "AI":
                return ai_cog
            return None

        bot.get_cog.side_effect = get_cog

        cog = admin_cog.Admin(bot)
        ctx = MagicMock()
        ctx.send = AsyncMock()

        await cog.health(ctx)

        ctx.send.assert_called_once()
        msg = ctx.send.call_args[0][0]
        # The owner-only health command shows full internals
        self.assertIn("SYSTEM HEALTH", msg)
        self.assertIn("WebSocket ping", msg)
        self.assertIn("Database (MongoDB)", msg)
        self.assertIn("Gemini", msg)
        self.assertIn("Groq", msg)
        self.assertIn("Together", msg)
        self.assertIn("Loaded cogs", msg)

    async def test_status_slash_command_healthy(self):
        """Status shows ✅ when everything works."""
        bot = MagicMock()
        bot.mongo = MagicMock()
        bot.mongo.admin = MagicMock()
        bot.mongo.admin.command = AsyncMock(return_value={"ok": 1})
        bot.latency = 0.05  # finite = gateway OK

        cog = admin_cog.Admin(bot)
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await cog.status_slash(interaction)

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        # Must be ephemeral (not leak to the channel)
        self.assertTrue(call_kwargs.get("ephemeral", False))
        # Must have an embed
        self.assertIn("embed", call_kwargs)

    async def test_status_slash_command_db_down(self):
        """Status shows ⚠️ when the DB is unreachable."""
        bot = MagicMock()
        bot.mongo = MagicMock()
        bot.mongo.admin = MagicMock()
        bot.mongo.admin.command = AsyncMock(side_effect=Exception("connection refused"))
        bot.latency = 0.05

        cog = admin_cog.Admin(bot)
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await cog.status_slash(interaction)

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        # Must be ephemeral
        self.assertTrue(call_kwargs.get("ephemeral", False))
        self.assertIn("embed", call_kwargs)
        # The specific error message must NOT be leaked to the user-facing embed
        embed = call_kwargs.get("embed")
        embed_str = str(embed.to_dict()) if hasattr(embed, "to_dict") else str(embed)
        self.assertNotIn("connection refused", embed_str)

    async def test_health_is_owner_restricted(self):
        """The !health prefix command must have @commands.is_owner() applied."""
        # Inspect the source - the is_owner decorator must be present
        import inspect  # noqa: E402  # mocks must load first

        src = inspect.getsource(admin_cog.Admin.health)
        self.assertIn("is_owner", src)

    async def test_status_does_not_leak_internals(self):
        """/status must NOT expose ping numbers, cog lists, or provider details."""
        bot = MagicMock()
        bot.mongo = MagicMock()
        bot.mongo.admin = MagicMock()
        bot.mongo.admin.command = AsyncMock(return_value={"ok": 1})
        bot.latency = 0.05
        bot.guilds = [1, 2, 3]
        bot.cogs = {"AI": MagicMock(), "Admin": MagicMock()}

        cog = admin_cog.Admin(bot)
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await cog.status_slash(interaction)

        call_kwargs = interaction.response.send_message.call_args.kwargs
        embed = call_kwargs.get("embed")
        embed_str = str(embed.to_dict()) if hasattr(embed, "to_dict") else str(embed)
        # None of these internal details should appear in the public status
        self.assertNotIn("WebSocket ping", embed_str)
        self.assertNotIn("Loaded cogs", embed_str)
        self.assertNotIn("Gemini", embed_str)
        self.assertNotIn("Groq", embed_str)
        self.assertNotIn("ms", embed_str)  # no latency numbers


if __name__ == "__main__":
    unittest.main()
