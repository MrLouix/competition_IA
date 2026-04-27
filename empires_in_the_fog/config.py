"""Empires in the Fog — Game configuration."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class GameConfig:
    """Configuration values for a game instance."""
    MAX_MESSAGES_PER_TURN: int = 1
    MAX_MESSAGE_WORDS: int = 10
    TIMEOUT_TURN_SECONDS: int = 60
    BASE_COST: float = 0.5
    ATTRITION_RATE: float = 0.1          # food per unit per minute
    SEMANTIC_THRESHOLD_ADJACENT: float = 0.35
    SEMANTIC_THRESHOLD_THEME: float = 0.20
    THEME_BONUS: float = 1.5
    fog_reveal_radius: int = 2
    MAX_TURNS: int = 20
    victory_condition: str = "either"     # domination | score | either
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    unit_costs: dict[str, int] = field(default_factory=lambda: {
        "scout": 2, "infantry": 4, "cavalry": 6, "artillery": 8,
    })
    terrain_income: dict[str, int] = field(default_factory=lambda: {
        "capital": 3, "city": 2, "village": 1, "fort": 1, "plain": 0, "forest": 0,
    })
    terrain_bonus: dict[str, float] = field(default_factory=lambda: {
        "fort": 1.5, "city": 1.5, "village": 1.0, "plain": 1.0, "forest": 1.2, "open": 0.8,
    })
    elite_unit_types: list[str] = field(default_factory=list)
    global_theme: str = "alliance"
    initial_verse: str = "Dans le brouillard, les empires se cherchent."


# Default board templates (13 hexes)
DEFAULT_BOARD_TEMPLATES: list[dict] = [
    {"hex_id": "h0_0",   "q": 0,  "r": 0,   "terrain": "capital"},
    {"hex_id": "h1_0",   "q": 1,  "r": 0,   "terrain": "plain"},
    {"hex_id": "h-1_0",  "q": -1, "r": 0,   "terrain": "forest"},
    {"hex_id": "h0_1",   "q": 0,  "r": 1,   "terrain": "plain"},
    {"hex_id": "h0_-1",  "q": 0,  "r": -1,  "terrain": "plain"},
    {"hex_id": "h1_-1",  "q": 1,  "r": -1,  "terrain": "village"},
    {"hex_id": "h-1_1",  "q": -1, "r": 1,   "terrain": "village"},
    {"hex_id": "h1_1",   "q": 1,  "r": 1,   "terrain": "forest"},
    {"hex_id": "h-1_-1", "q": -1, "r": -1,  "terrain": "plain"},
    {"hex_id": "h0_2",   "q": 0,  "r": 2,   "terrain": "city"},
    {"hex_id": "h0_-2",  "q": 0,  "r": -2,  "terrain": "city"},
    {"hex_id": "h2_-1",  "q": 2,  "r": -1,  "terrain": "plain"},
    {"hex_id": "h-2_1",  "q": -2, "r": 1,   "terrain": "plain"},
]
