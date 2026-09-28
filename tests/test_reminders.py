"""Tests for cogs/reminders.py - focused on the time parser, which is the
most logic-heavy piece of the new /remind command.

The slash command itself is thin glue around parse_time_to_seconds + a
MongoDB insert + a sweep loop, so we test the parser directly + add a
source-level assertion that the new commands exist in general.py.
"""

import os  # noqa: E402  # mocks must load first
import sys  # noqa: E402  # mocks must load first
import unittest  # noqa: E402  # mocks must load first
from unittest.mock import MagicMock  # noqa: E402  # mocks must load first

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Other test files (test_general.py, test_social.py) may have already installed
# their own MockCog into sys.modules['discord.ext.commands'].Cog - and their
# MockCog lacks the .listener() classmethod that cogs/reactionroles.py needs at
# import time. To make this test file order-independent, we:
#   1. Force-reset the discord.* mock modules with our own MockCog that HAS listener
#   2. Delete any cached cogs.reactionroles / cogs.reminders so they re-import
#      against our (correct) mocks rather than reusing a broken cached version.


class MockCog:
    @classmethod
    def listener(cls):
        def decorator(func):
            return func

        return decorator

    def __init__(self, *args, **kwargs):
        pass


mock_commands = MagicMock()
mock_commands.Cog = MockCog


def _passthrough_decorator(*args, **kwargs):
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def deco(fn):
        return fn

    return deco


mock_commands.command = _passthrough_decorator
mock_commands.is_owner = _passthrough_decorator

mock_ext = MagicMock()
mock_ext.commands = mock_commands

# Force-reset - overwrite any mocks installed by other test files
sys.modules["discord"] = MagicMock()
sys.modules["discord.ext"] = mock_ext
sys.modules["discord.ext.commands"] = mock_commands
sys.modules["discord.ext.tasks"] = MagicMock()
sys.modules["discord.app_commands"] = MagicMock()

# Delete cached cog modules so they re-import against our (correct) mocks
for _mod in ("cogs.reactionroles", "cogs.reminders"):
    sys.modules.pop(_mod, None)

# Now we can safely import the parser + reaction-role helpers.
# IMPORTANT: import cogs.reactionroles HERE at module load time, right after the
# mock setup. If we defer the import to inside test methods, other test files
# (test_general.py / test_social.py) will have re-installed their broken MockCog
# (without .listener()) by then, and the @commands.Cog.listener() decorator in
# cogs/reactionroles.py will raise AttributeError on import.
from cogs.reactionroles import parse_entries, resolve_role  # noqa: E402  # mocks must load first
from cogs.reminders import (  # noqa: E402  # mocks must load first
    MAX_REMINDER_DELAY_SECS,
    parse_time_to_seconds,
)


class TestTimeParser(unittest.TestCase):
    """parse_time_to_seconds must handle a variety of human-friendly formats."""

    def test_bare_minutes(self):
        # "30" alone should be treated as 30 minutes (common shorthand)
        self.assertEqual(parse_time_to_seconds("30"), 30 * 60)

    def test_minutes_suffix(self):
        self.assertEqual(parse_time_to_seconds("10m"), 600)
        self.assertEqual(parse_time_to_seconds("10 m"), 600)
        self.assertEqual(parse_time_to_seconds("10min"), 600)

    def test_hours(self):
        self.assertEqual(parse_time_to_seconds("2h"), 2 * 3600)
        self.assertEqual(parse_time_to_seconds("2 h"), 2 * 3600)

    def test_days(self):
        self.assertEqual(parse_time_to_seconds("1d"), 86400)
        self.assertEqual(parse_time_to_seconds("3d"), 3 * 86400)

    def test_seconds(self):
        self.assertEqual(parse_time_to_seconds("45s"), 45)

    def test_compound(self):
        # 1h30m = 5400 seconds
        self.assertEqual(parse_time_to_seconds("1h30m"), 5400)
        # 2d4h = 2*86400 + 4*3600 = 187200
        self.assertEqual(parse_time_to_seconds("2d4h"), 187200)
        # 1d2h30m15s
        self.assertEqual(
            parse_time_to_seconds("1d2h30m15s"),
            86400 + 2 * 3600 + 30 * 60 + 15,
        )

    def test_case_insensitive(self):
        self.assertEqual(parse_time_to_seconds("2H"), 2 * 3600)
        self.assertEqual(parse_time_to_seconds("1D2H"), 86400 + 2 * 3600)

    def test_empty_returns_none(self):
        self.assertIsNone(parse_time_to_seconds(""))
        self.assertIsNone(parse_time_to_seconds(None))

    def test_zero_returns_none(self):
        # Zero duration is meaningless for a reminder
        self.assertIsNone(parse_time_to_seconds("0"))
        self.assertIsNone(parse_time_to_seconds("0m"))
        self.assertIsNone(parse_time_to_seconds("0h0m0s"))

    def test_garbage_returns_none(self):
        self.assertIsNone(parse_time_to_seconds("banana"))
        self.assertIsNone(parse_time_to_seconds("h"))
        self.assertIsNone(parse_time_to_seconds("abc123"))

    def test_max_delay_is_30_days(self):
        # The parser itself doesn't enforce the cap, but the cog does -
        # here we just verify the constant is set to 30 days.
        self.assertEqual(MAX_REMINDER_DELAY_SECS, 30 * 86400)


class TestNewCommandsExist(unittest.TestCase):
    """Source-level assertions that each new command is wired up correctly."""

    @staticmethod
    def _read(path):
        full = os.path.join(os.path.dirname(__file__), "..", path)
        with open(full, encoding="utf-8") as f:
            return f.read()

    def test_history_command_exists(self):
        src = self._read("cogs/general.py")
        self.assertIn('name="history"', src)
        # /history must be ephemeral (privacy: it shows the user's own data)
        self.assertIn("ephemeral=True", src)

    def test_forgive_command_exists(self):
        src = self._read("cogs/general.py")
        self.assertIn('name="forgive"', src)

    def test_mood_command_exists(self):
        src = self._read("cogs/general.py")
        self.assertIn('name="mood"', src)
        # /mood must surface grudge state
        self.assertIn("grudge_collection", src)

    def test_8ball_command_exists(self):
        src = self._read("cogs/general.py")
        self.assertIn('name="8ball"', src)
        # Must have cooldown (spam guard)
        self.assertIn("checks.cooldown", src)

    def test_translate_command_exists(self):
        src = self._read("cogs/general.py")
        self.assertIn('name="translate"', src)
        # Must have language choices built from _SUPPORTED_LANGUAGES
        self.assertIn("_SUPPORTED_LANGUAGES", src)
        self.assertIn("app_commands.Choice", src)

    def test_avatar_command_exists(self):
        src = self._read("cogs/general.py")
        self.assertIn('name="avatar"', src)

    def test_remind_command_exists(self):
        src = self._read("cogs/reminders.py")
        self.assertIn('name="remind"', src)
        # Must persist to MongoDB (not in-memory)
        self.assertIn("reminders_collection", src)
        # Must have a sweep loop
        self.assertIn("@tasks.loop", src)

    def test_setuproles_command_exists(self):
        src = self._read("cogs/reactionroles.py")
        self.assertIn('name="setuproles"', src)
        # Must require manage_roles permission
        self.assertIn("has_permissions(manage_roles=True)", src)
        # Must listen for reaction events
        self.assertIn("on_raw_reaction_add", src)
        self.assertIn("on_raw_reaction_remove", src)

    def test_help_lists_new_commands(self):
        src = self._read("cogs/general.py")
        for cmd in [
            "/history",
            "/forgive",
            "/mood",
            "/8ball",
            "/translate",
            "/avatar",
            "/remind",
            "/setuproles",
        ]:
            self.assertIn(cmd, src, f"/help must list {cmd}")

    def test_main_py_registers_new_collections(self):
        src = self._read("main.py")
        self.assertIn("reminders_collection", src)
        self.assertIn("reaction_roles_col", src)


class TestReactionRoleParsing(unittest.TestCase):
    """Test the entries parser in cogs/reactionroles.py.

    The parser turns a string like '🔴:@Red 🟢:@Green' into a list of
    (emoji_str, role) tuples. We test it in isolation with a fake guild.
    We don't instantiate the cog (it requires heavy discord.py mocking);
    instead we test _parse_entries by binding it to a bare object and
    _resolve_role as a staticmethod directly.
    """

    def setUp(self):
        # Build a minimal fake guild with two roles
        class FakeRole:
            def __init__(self, rid, name, managed=False):
                self.id = rid
                self.name = name
                self.managed = managed
                self.mention = f"<@&{rid}>"

        self.red_role = FakeRole(1001, "Red")
        self.green_role = FakeRole(1002, "Green")

        self.guild = type("FakeGuild", (), {})()
        self.guild.get_role = lambda rid: {
            1001: self.red_role,
            1002: self.green_role,
        }.get(rid)

    def _parse(self, text):
        """Call the module-level parse_entries directly (no cog instance needed)."""
        return parse_entries(text, self.guild)

    def test_parses_two_entries(self):
        result = self._parse("🔴:<@&1001> 🟢:<@&1002>")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][0], "🔴")
        self.assertEqual(result[0][1].id, 1001)
        self.assertEqual(result[1][0], "🟢")
        self.assertEqual(result[1][1].id, 1002)

    def test_handles_raw_role_ids(self):
        result = self._parse("🔴:1001")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1].id, 1001)

    def test_skips_unparseable_tokens(self):
        # Token without ':' should be skipped
        result = self._parse("garbage 🔴:<@&1001>")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1].id, 1001)

    def test_skips_invalid_role_ids(self):
        # Non-existent role ID → get_role returns None → entry skipped
        result = self._parse("🔴:<@&9999>")
        self.assertEqual(len(result), 0)

    def test_resolve_role_returns_none_for_garbage(self):
        self.assertIsNone(resolve_role("banana", self.guild))
        self.assertIsNone(resolve_role("", self.guild))
        self.assertIsNone(resolve_role("<@&abc>", self.guild))

    def test_resolve_role_finds_valid_id(self):
        self.assertEqual(resolve_role("<@&1001>", self.guild), self.red_role)
        self.assertEqual(resolve_role("1001", self.guild), self.red_role)


if __name__ == "__main__":
    unittest.main()
