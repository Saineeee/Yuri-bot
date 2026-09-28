"""Tests for cogs/tools.py - the function-calling tools (web_search, get_time_in_timezone, calculate).

These are pure-logic tests (no Discord, no Gemini) that verify:
  - calculate() handles valid + invalid expressions correctly
  - get_time_in_timezone() handles valid + unknown timezones
  - dispatch_tool() routes to the right handler
  - get_tool_declarations() returns the expected set
"""

import os  # noqa: E402  # mocks must load first
import sys  # noqa: E402  # mocks must load first
import unittest  # noqa: E402  # mocks must load first

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock heavy deps before importing the cog
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402  # mocks must load first

sys.modules["discord"] = MagicMock()
sys.modules["discord.ext"] = MagicMock()
sys.modules["discord.ext.commands"] = MagicMock()
sys.modules["discord.ext.tasks"] = MagicMock()
sys.modules["discord.app_commands"] = MagicMock()
sys.modules["pytz"] = __import__("pytz")  # use real pytz for timezone tests

from cogs.tools import (  # noqa: E402  # mocks must load first
    TOOL_REGISTRY,
    calculate,
    dispatch_tool,
    get_time_in_timezone,
    get_tool_declarations,
    web_search,
)


class TestCalculate(unittest.TestCase):
    """calculate() must handle valid math and reject dangerous input."""

    def test_simple_arithmetic(self):
        self.assertEqual(calculate("2 + 3"), "5")
        self.assertEqual(calculate("10 - 4"), "6")
        self.assertEqual(calculate("6 * 7"), "42")
        self.assertEqual(calculate("20 / 4"), "5.0")

    def test_order_of_operations(self):
        self.assertEqual(calculate("2 + 3 * 4"), "14")
        self.assertEqual(calculate("(2 + 3) * 4"), "20")

    def test_decimal(self):
        self.assertEqual(calculate("3.5 + 1.5"), "5.0")

    def test_math_functions(self):

        self.assertEqual(calculate("sqrt(144)"), "12.0")
        self.assertAlmostEqual(float(calculate("log(e)")), 1.0, places=10)
        self.assertAlmostEqual(float(calculate("sin(0)")), 0.0, places=10)

    def test_constants(self):
        # pi and e should be available
        result = calculate("pi")
        import math  # noqa: E402  # mocks must load first

        self.assertAlmostEqual(float(result), math.pi, places=10)

    def test_empty_returns_error(self):
        self.assertIn("Error", calculate(""))
        self.assertIn("Error", calculate("   "))

    def test_rejects_imports(self):
        # Must not allow __import__ or attribute access
        result = calculate("__import__('os')")
        self.assertIn("Error", result)

    def test_rejects_names(self):
        result = calculate("open('file.txt')")
        self.assertIn("Error", result)

    def test_division_by_zero(self):
        result = calculate("1 / 0")
        self.assertIn("Error", result)


class TestGetTimeInTimezone(unittest.IsolatedAsyncioTestCase):
    """get_time_in_timezone() must handle valid + unknown timezones."""

    def test_valid_timezone(self):
        result = get_time_in_timezone("Asia/Kolkata")
        # The format is "Monday, January 01, 2024 - 12:00 PM"
        # Just check it returns something with a date + time pattern
        self.assertIn(",", result)  # "Monday, January..."
        self.assertIn(":", result)  # time has a colon
        self.assertIn("AM", result.upper())  # AM or PM
        self.assertNotIn("Unknown", result)

    def test_common_city_name(self):
        # Should resolve "india" → Asia/Kolkata
        result = get_time_in_timezone("india")
        self.assertNotIn("Unknown", result)

    def test_unknown_timezone_returns_error(self):
        result = get_time_in_timezone("Mars/Olympus")
        self.assertIn("Unknown", result)

    def test_utc(self):
        result = get_time_in_timezone("UTC")
        self.assertNotIn("Unknown", result)


class TestWebSearch(unittest.IsolatedAsyncioTestCase):
    """web_search() delegates to utils.search_web."""

    async def test_returns_string(self):
        with patch("utils.search_web", new=AsyncMock(return_value="fake results")):
            result = await web_search("test query")
            self.assertEqual(result, "fake results")

    async def test_returns_no_results_message(self):
        with patch("utils.search_web", new=AsyncMock(return_value=None)):
            result = await web_search("test query")
            self.assertIn("No web results", result)


class TestDispatchTool(unittest.IsolatedAsyncioTestCase):
    """dispatch_tool() must route to the right handler."""

    async def test_dispatches_calculate(self):
        result = await dispatch_tool("calculate", {"expression": "2 + 2"})
        self.assertEqual(result, "4")

    async def test_dispatches_unknown_tool(self):
        result = await dispatch_tool("nonexistent_tool", {})
        self.assertIn("unknown tool", result)

    async def test_dispatches_with_bad_args(self):
        # calculate() expects 'expression', pass wrong arg
        result = await dispatch_tool("calculate", {"wrong_arg": "2+2"})
        self.assertIn("Error", result)


class TestToolRegistry(unittest.TestCase):
    """The tool registry must expose all tools consistently."""

    def test_registry_has_three_tools(self):
        self.assertEqual(len(TOOL_REGISTRY), 3)
        self.assertIn("web_search", TOOL_REGISTRY)
        self.assertIn("get_time_in_timezone", TOOL_REGISTRY)
        self.assertIn("calculate", TOOL_REGISTRY)

    def test_each_tool_has_declaration_and_handler(self):
        for name, (decl, handler) in TOOL_REGISTRY.items():
            self.assertIsInstance(decl, dict, f"{name} declaration must be a dict")
            self.assertIn("name", decl, f"{name} declaration must have a name")
            self.assertEqual(decl["name"], name)
            self.assertIn("description", decl, f"{name} declaration must have a description")
            self.assertIn("parameters", decl, f"{name} declaration must have parameters")
            self.assertTrue(callable(handler), f"{name} handler must be callable")

    def test_get_tool_declarations_returns_list(self):
        decls = get_tool_declarations()
        self.assertIsInstance(decls, list)
        self.assertEqual(len(decls), 3)
        names = [d["name"] for d in decls]
        self.assertIn("web_search", names)
        self.assertIn("get_time_in_timezone", names)
        self.assertIn("calculate", names)


if __name__ == "__main__":
    unittest.main()
