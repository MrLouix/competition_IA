"""DEPRECATED — All tools moved to server.py with FastMCP decorators. This file is kept for backward compat only."""
# This module is obsolete — all tool definitions were moved to server.py
# using FastMCP's @tool decorator. Do not import from here.


async def get_tool_definitions() -> list[Tool]:
    """Return MCP tool definitions including get_game_rules."""
    return [
        Tool(
            name="get_game_rules",
            description=(
                "Retrieve the complete Empires in the Fog game rules in AI-readable markdown format. "
                "Contains all mechanics: turn system, food/attrition, combat, semantic diplomacy, "
                "victory conditions, MCP API endpoints, configuration defaults, unit types, and more. "
                "Call this first before playing to understand the game."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "player_id": {
                        "type": "string",
                        "description": "The player ID requesting the rules."
                    }
                },
                "required": ["player_id"],
            },
        ),
    ]


async def handle_tool_call(name: str, arguments: dict) -> list[TextContent]:
    """Handle MCP tool calls. Routes to the appropriate handler."""

    if name == "get_game_rules":
        return await _get_game_rules(arguments)

    raise ValueError(f"Unknown tool: {name}")


async def _get_game_rules(arguments: dict) -> list[TextContent]:
    """Return game rules as AI-readable markdown."""
    player_id = arguments.get("player_id", "unknown")

    try:
        rules_text = RULES_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [TextContent(
            type="text",
            text=(
                f"ERROR: Game rules file not found at {RULES_FILE}. "
                f"Please contact the game administrator."
            ),
        )]

    # Insert current GameConfig values dynamically if a game is active
    # (This would be wired to the active GameState in a full implementation)
    header = (
        f"# Empires in the Fog - Game Rules (Player: {player_id})\n\n"
        f"> *These are the official game rules. Configuration values "
        f"shown are the defaults for this game instance.*\n\n"
    )

    return [TextContent(
        type="text",
        text=header + rules_text,
    )]
