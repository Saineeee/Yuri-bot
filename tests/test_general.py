"""Tests for cogs/general.py and cogs/admin.py - source-level regression checks."""

import os
import re
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestStatusLoopFix(unittest.TestCase):
    """The status_loop must set an explicit, deliberate presence status.

    Source-level assertion is the most reliable check here: the original bug
    was Status.idle making the bot look unavailable (yellow dot). dnd is a
    deliberate choice for Yuri's persona.
    """

    def test_status_loop_uses_online_not_idle(self):
        path = os.path.join(os.path.dirname(__file__), "..", "cogs", "general.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()

        self.assertNotIn(
            "discord.Status.idle",
            src,
            "status_loop must not use Status.idle, it makes the bot appear "
            "unavailable (yellow dot) in the member list.",
        )
        self.assertIn(
            "status=discord.Status.",
            src,
            "status_loop should set an explicit status (online or dnd).",
        )


class TestAdminWipeallConfirmation(unittest.TestCase):
    """!wipeall requires a 'confirm' argument; !inbox is DM-only."""

    def test_wipeall_requires_confirmation(self):
        path = os.path.join(os.path.dirname(__file__), "..", "cogs", "admin.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()

        self.assertIn(
            "confirm",
            src,
            "!wipeall must require a 'confirm' argument before wiping the DB.",
        )

    def test_inbox_is_dm_only(self):
        path = os.path.join(os.path.dirname(__file__), "..", "cogs", "admin.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()

        self.assertIn(
            "DM-only",
            src,
            "!inbox must be DM-only to prevent leaking feedback in public channels.",
        )


class TestAIFixes(unittest.TestCase):
    """Verify the AI cog fixes are in place."""

    def test_fromrgb_typo_is_gone(self):
        path = os.path.join(os.path.dirname(__file__), "..", "cogs", "ai.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()

        self.assertNotIn(
            "Color.fromrgb(",
            src,
            "The fromrgb typo (no underscore) crashes on GIF responses. "
            "All call sites must use from_rgb (with underscore).",
        )

    def test_safety_filters_not_all_block_none(self):
        path = os.path.join(os.path.dirname(__file__), "..", "cogs", "ai.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()

        # HARM_CATEGORY_SEXUALLY_EXPLICIT must NOT be BLOCK_NONE,
        # that's the most dangerous category to leave unguarded.
        self.assertIsNone(
            re.search(
                r"HARM_CATEGORY_SEXUALLY_EXPLICIT,\s*threshold=types.HarmBlockThreshold.BLOCK_NONE",
                src,
            ),
            "Sexually explicit content must have at least BLOCK_ONLY_HIGH guard.",
        )
        self.assertIsNotNone(
            re.search(
                r"HARM_CATEGORY_SEXUALLY_EXPLICIT,\s*threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH",
                src,
            ),
            "Sexually explicit content should use BLOCK_ONLY_HIGH.",
        )

    def test_consolidated_groq_key_helper(self):
        path = os.path.join(os.path.dirname(__file__), "..", "cogs", "ai.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()

        # duplicate key-rotation helpers were consolidated into _cycle_groq_key
        self.assertIn("_cycle_groq_key", src)
        self.assertNotIn("def _advance_groq_key", src)
        self.assertNotIn("def _rotate_groq_key", src)


if __name__ == "__main__":
    unittest.main()
