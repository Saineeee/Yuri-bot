"""Function-calling tools for Gemini: web_search, get_time_in_timezone, calculate.

Each tool is a declaration dict sent to the model plus a Python handler.
The AI cog dispatches FunctionCall responses here via dispatch_tool().
"""

import asyncio
import logging
import math
import re

import pytz

import utils

log = logging.getLogger(__name__)


WEB_SEARCH_DECL = {
    "name": "web_search",
    "description": (
        "Search the web for up-to-date information (news, prices, facts, current events). "
        "Use this when the user asks about something that may have changed recently or "
        "that you don't have reliable knowledge about. Returns 2 short snippets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query, be specific and concise.",
            },
        },
        "required": ["query"],
    },
}


async def web_search(query: str) -> str:
    """Run a DuckDuckGo text search and return formatted results."""
    result = await utils.search_web(query)
    return result or "No web results found."


_TIMEZONE_DECL = {
    "name": "get_time_in_timezone",
    "description": (
        "Get the current date and time in a specific timezone. Use this when the user "
        "asks what time it is somewhere, or needs time-related context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": (
                    "A timezone name like 'Asia/Kolkata', 'America/New_York', 'Europe/London', "
                    "'Asia/Tokyo'. If unsure, use 'UTC'."
                ),
            },
        },
        "required": ["timezone"],
    },
}


def get_time_in_timezone(timezone: str) -> str:
    """Return the current time in the named timezone."""
    import datetime

    try:
        tz = pytz.timezone(timezone)
    except pytz.UnknownTimeZoneError:
        # fuzzy match common city/zone names
        common = {
            "india": "Asia/Kolkata",
            "mumbai": "Asia/Kolkata",
            "delhi": "Asia/Kolkata",
            "ist": "Asia/Kolkata",
            "japan": "Asia/Tokyo",
            "tokyo": "Asia/Tokyo",
            "jst": "Asia/Tokyo",
            "us": "America/New_York",
            "new york": "America/New_York",
            "est": "America/New_York",
            "pst": "America/Los_Angeles",
            "los angeles": "America/Los_Angeles",
            "uk": "Europe/London",
            "london": "Europe/London",
            "gmt": "Europe/London",
            "paris": "Europe/Paris",
            "cet": "Europe/Paris",
            "dubai": "Asia/Dubai",
            "gst": "Asia/Dubai",
            "australia": "Australia/Sydney",
            "sydney": "Australia/Sydney",
        }
        tz_name = common.get(timezone.lower().strip())
        if tz_name is None:
            return f"Unknown timezone '{timezone}'. Try 'Asia/Kolkata' or 'America/New_York'."
        tz = pytz.timezone(tz_name)
    now = datetime.datetime.now(tz)
    return now.strftime("%A, %B %d, %Y - %I:%M %p")


# only digits, basic operators, parens, dots, commas and letters survive the
# first pass; every letter run must then be a whitelisted function name
_MATH_CHARS_RE = re.compile(r"^[\d\s\+\-\*/\(\)\.,a-zA-Z]*$")
_ALLOWED_MATH_TOKENS = {
    "sqrt",
    "sin",
    "cos",
    "tan",
    "log",
    "log10",
    "exp",
    "pi",
    "e",
    "abs",
    "round",
    "floor",
    "ceil",
    "pow",
    "min",
    "max",
}

_CALC_DECL = {
    "name": "calculate",
    "description": (
        "Evaluate a math expression and return the result. Supports +, -, *, /, "
        "parentheses, and functions: sqrt, sin, cos, tan, log, exp, pow, abs, round, "
        "floor, ceil, min, max. Constants: pi, e."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The math expression to evaluate, e.g. '2 + 3 * 4' or 'sqrt(144)'.",
            },
        },
        "required": ["expression"],
    },
}


def calculate(expression: str) -> str:
    """Evaluate a math expression with a restricted eval.

    No builtins, and only whitelisted function names make it past the
    pre-filter, so imports/attribute access can't sneak in.
    """
    expr = expression.strip()
    if not expr:
        return "Error: empty expression"

    if not _MATH_CHARS_RE.fullmatch(expr):
        return "Error: expression contains disallowed characters"

    for token in re.findall(r"[a-zA-Z]+", expr):
        if token not in _ALLOWED_MATH_TOKENS:
            return "Error: expression contains disallowed characters"

    safe_globals = {
        "__builtins__": {},
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "log": math.log,
        "log10": math.log10,
        "exp": math.exp,
        "pi": math.pi,
        "e": math.e,
        "abs": abs,
        "round": round,
        "floor": math.floor,
        "ceil": math.ceil,
        "pow": pow,
        "min": min,
        "max": max,
    }
    try:
        result = eval(expr, safe_globals, {})  # restricted eval, see above
        return f"{result}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# name -> (declaration dict, handler)
TOOL_REGISTRY = {
    "web_search": (WEB_SEARCH_DECL, web_search),
    "get_time_in_timezone": (_TIMEZONE_DECL, get_time_in_timezone),
    "calculate": (_CALC_DECL, calculate),
}


async def dispatch_tool(name: str, args: dict) -> str:
    """Run a tool by name; async handlers are awaited, sync ones go to a thread."""
    if name not in TOOL_REGISTRY:
        return f"Error: unknown tool '{name}'"
    _, handler = TOOL_REGISTRY[name]
    try:
        if asyncio.iscoroutinefunction(handler):
            result = await handler(**args)
        else:
            result = await asyncio.to_thread(handler, **args)
        return str(result)
    except Exception as e:
        log.warning("tool '%s' failed: %s: %s", name, type(e).__name__, e)
        return f"Error: {type(e).__name__}: {e}"


def get_tool_declarations() -> list[dict]:
    """Declaration dicts for Gemini, in registry order."""
    return [decl for decl, _ in TOOL_REGISTRY.values()]
