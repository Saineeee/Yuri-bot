"""Tests for cogs/levelling.py - XP curve + level computation.

The levelling cog's core math (xp_for_level, level_from_xp) is pure and
testable without Discord. We verify the curve is monotonic and the
round-trip (xp → level → xp) is consistent.
"""

import os  # noqa: E402  # mocks must load first
import sys  # noqa: E402  # mocks must load first
import unittest  # noqa: E402  # mocks must load first

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock heavy deps before importing the cog
from unittest.mock import MagicMock  # noqa: E402  # mocks must load first

sys.modules["discord"] = MagicMock()
sys.modules["discord.ext"] = MagicMock()
sys.modules["discord.ext.commands"] = MagicMock()
sys.modules["discord.ext.tasks"] = MagicMock()
sys.modules["discord.app_commands"] = MagicMock()


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
mock_ext = MagicMock()
mock_ext.commands = mock_commands

# Force-reset so we use our mock
sys.modules["discord.ext"] = mock_ext
sys.modules["discord.ext.commands"] = mock_commands
sys.modules["discord.ext.tasks"] = MagicMock()
sys.modules["discord.app_commands"] = MagicMock()

# Delete any cached import
sys.modules.pop("cogs.levelling", None)

from cogs.levelling import (  # noqa: E402  # mocks must load first
    XP_PER_MESSAGE,
    level_from_xp,
    xp_for_level,
)


class TestXPCurve(unittest.TestCase):
    """Verify the XP curve is well-behaved."""

    def test_level_0_requires_no_xp(self):
        # Level 0 is the starting level - 0 XP needed.
        self.assertEqual(xp_for_level(0), 0)
        # Level 1: 5*1 + 50*1 = 55
        self.assertEqual(xp_for_level(1), 55)
        # Level 2: 5*4 + 50*2 = 20 + 100 = 120
        self.assertEqual(xp_for_level(2), 120)
        # Level 5: 5*25 + 50*5 = 125 + 250 = 375
        self.assertEqual(xp_for_level(5), 375)
        # Negative levels also return 0 (defensive)
        self.assertEqual(xp_for_level(-1), 0)

    def test_curve_is_monotonic(self):
        """Each level must require more XP than the previous."""
        prev = 0
        for level in range(1, 100):
            current = xp_for_level(level)
            self.assertGreater(
                current, prev, f"Level {level} must require more XP than level {level-1}"
            )
            prev = current

    def test_level_from_xp_at_boundary(self):
        """At exactly the XP threshold, you should be at that level."""
        # If you have exactly xp_for_level(5) XP, you should be level 5
        xp = xp_for_level(5)
        level, xp_into = level_from_xp(xp)
        self.assertEqual(level, 5)
        self.assertEqual(xp_into, 0)

    def test_level_from_xp_below_boundary(self):
        """One XP below a threshold should keep you at the previous level."""
        xp = xp_for_level(5) - 1
        level, xp_into = level_from_xp(xp)
        self.assertEqual(level, 4)

    def test_level_from_xp_zero(self):
        """0 XP → level 0."""
        level, xp_into = level_from_xp(0)
        self.assertEqual(level, 0)
        self.assertEqual(xp_into, 0)

    def test_level_from_xp_negative(self):
        """Negative XP should be treated as level 0 (defensive)."""
        level, _ = level_from_xp(-100)
        self.assertEqual(level, 0)

    def test_round_trip(self):
        """For any XP value, the level + xp_into should sum back to the original."""
        for xp in [0, 50, 100, 155, 300, 875, 1000, 5000]:
            level, xp_into = level_from_xp(xp)
            reconstructed = xp_for_level(level) + xp_into
            self.assertEqual(reconstructed, xp, f"Round-trip failed for xp={xp}")

    def test_xp_per_message_is_positive(self):
        """The base XP per message must be positive."""
        self.assertGreater(XP_PER_MESSAGE, 0)


if __name__ == "__main__":
    unittest.main()
