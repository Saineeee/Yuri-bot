"""Tests for the new feature cogs - source-level assertions.

Verifies that all the new cogs (memory, imagegen, starboard, levelling,
voicetts, tools) are properly structured with their key commands, listeners,
and sweep loops. Uses source-code inspection to avoid heavy Discord mocking.
"""

import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestNewCogsExist(unittest.TestCase):
    """Verify all new cog files exist and contain the expected commands/listeners."""

    @staticmethod
    def _read(path):
        full = os.path.join(os.path.dirname(__file__), "..", path)
        with open(full, encoding="utf-8") as f:
            return f.read()

    # --- Memory summarization ---

    def test_memory_cog_exists(self):
        src = self._read("cogs/memory.py")
        self.assertIn("class MemorySummarizer", src)
        self.assertIn("get_user_dossier_text", src)
        # Must have a sweep loop
        self.assertIn("@tasks.loop", src)
        # Must summarize old messages
        self.assertIn("SUMMARIZE_AGE_DAYS", src)
        self.assertIn("memory_dossiers_col", src)

    def test_memory_wired_into_ai_cog(self):
        src = self._read("cogs/ai.py")
        # The AI cog must fetch the dossier from MemorySummarizer
        self.assertIn("MemorySummarizer", src)
        self.assertIn("get_user_dossier_text", src)
        self.assertIn("LONG-TERM MEMORY", src)

    # --- Image generation ---

    def test_imagegen_cog_exists(self):
        src = self._read("cogs/imagegen.py")
        self.assertIn("class ImageGen", src)
        self.assertIn('name="imagine"', src)
        # Must use Imagen
        self.assertIn("imagen", src.lower())
        # Must have cooldown
        self.assertIn("checks.cooldown", src)

    # --- Starboard ---

    def test_starboard_cog_exists(self):
        src = self._read("cogs/starboard.py")
        self.assertIn("class Starboard", src)
        self.assertIn('name="starboard"', src)
        # Must listen for reactions
        self.assertIn("on_raw_reaction_add", src)
        self.assertIn("on_raw_reaction_remove", src)
        # Must have a threshold
        self.assertIn("threshold", src)

    # --- Levelling ---

    def test_levelling_cog_exists(self):
        src = self._read("cogs/levelling.py")
        self.assertIn("class Levelling", src)
        self.assertIn('name="rank"', src)
        self.assertIn('name="leaderboard"', src)
        self.assertIn('name="rankroles"', src)
        # Must award XP on message
        self.assertIn("on_message", src)
        self.assertIn("xp_collection", src)

    # --- Voice TTS ---

    def test_voicetts_cog_exists(self):
        src = self._read("cogs/voicetts.py")
        self.assertIn("class VoiceTTS", src)
        self.assertIn('name="say"', src)
        self.assertIn('name="vc"', src)
        # Must handle voice state updates (auto-disconnect when alone)
        self.assertIn("on_voice_state_update", src)

    # --- Tools / function calling ---

    def test_tools_cog_exists(self):
        src = self._read("cogs/tools.py")
        self.assertIn("TOOL_REGISTRY", src)
        self.assertIn("web_search", src)
        self.assertIn("get_time_in_timezone", src)
        self.assertIn("calculate", src)
        self.assertIn("dispatch_tool", src)
        self.assertIn("get_tool_declarations", src)

    def test_tools_wired_into_ai_cog(self):
        src = self._read("cogs/ai.py")
        self.assertIn("gemini_config_with_tools", src)
        self.assertIn("_handle_function_calls", src)
        self.assertIn("dispatch_tool", src)

    # --- Streaming ---

    def test_streaming_in_ai_cog(self):
        src = self._read("cogs/ai.py")
        self.assertIn("get_combined_response_streaming", src)
        self.assertIn("_stream_gemini", src)
        self.assertIn("generate_content_stream", src)
        self.assertIn("_stream_response_to_message", src)
        self.assertIn("STREAM_FIRST_CHUNK_MIN_CHARS", src)

    # --- Infrastructure ---

    def test_sentry_integration_in_main(self):
        src = self._read("main.py")
        self.assertIn("SENTRY_DSN", src)
        self.assertIn("sentry_sdk", src)

    def test_sharding_in_main(self):
        src = self._read("main.py")
        self.assertIn("SHARD_COUNT", src)
        self.assertIn("shard_count=SHARD_COUNT", src)

    def test_slash_status_command(self):
        """/status must exist as a public slash command (replaces old /health)."""
        src = self._read("cogs/admin.py")
        self.assertIn('name="status"', src)
        self.assertIn("status_slash", src)

    def test_health_prefix_is_owner_restricted(self):
        """!health prefix command must be owner-restricted (shows full internals)."""
        src = self._read("cogs/admin.py")
        # The !health command must have @commands.is_owner()
        self.assertIn("@commands.is_owner()", src)
        # And must NOT have a public /health slash command anymore
        self.assertNotIn('name="health"', src)

    def test_dockerfile_exists(self):
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(__file__), "..", "Dockerfile")))

    def test_docker_compose_exists(self):
        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml"))
        )

    def test_pyproject_exists(self):
        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(__file__), "..", "pyproject.toml"))
        )

    def test_ci_workflow_exists(self):
        self.assertTrue(
            os.path.exists(
                os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "ci.yml")
            )
        )

    def test_env_example_exists(self):
        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(__file__), "..", ".env.example"))
        )

    def test_pyproject_has_ruff_config(self):
        src = self._read("pyproject.toml")
        self.assertIn("[tool.ruff]", src)
        self.assertIn("[tool.black]", src)
        self.assertIn("[tool.pytest.ini_options]", src)


class TestHelpListsAllCommands(unittest.TestCase):
    """Verify /help mentions every new command."""

    @staticmethod
    def _read_help():
        path = os.path.join(os.path.dirname(__file__), "..", "cogs", "general.py")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_help_lists_imagine(self):
        self.assertIn("/imagine", self._read_help())

    def test_help_lists_say(self):
        self.assertIn("/say", self._read_help())

    def test_help_lists_vc(self):
        self.assertIn("/vc", self._read_help())

    def test_help_lists_rank(self):
        self.assertIn("/rank", self._read_help())

    def test_help_lists_leaderboard(self):
        self.assertIn("/leaderboard", self._read_help())

    def test_help_lists_starboard(self):
        self.assertIn("/starboard", self._read_help())

    def test_help_lists_rankroles(self):
        self.assertIn("/rankroles", self._read_help())

    def test_help_lists_status(self):
        self.assertIn("/status", self._read_help())


if __name__ == "__main__":
    unittest.main()
