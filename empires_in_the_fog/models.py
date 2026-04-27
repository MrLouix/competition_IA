"""Empires in the Fog — Data models."""

from __future__ import annotations
import uuid
from dataclasses import dataclass, field


# ─── Unit types and stats ───────────────────────────────────────────────

class UnitType:
    SCOUT = "scout"
    INFANTRY = "infantry"
    CAVALRY = "cavalry"
    ARTILLERY = "artillery"


UNIT_STATS: dict[str, dict[str, float]] = {
    UnitType.SCOUT:     {"atk": 1, "def": 1, "mobility": 4},
    UnitType.INFANTRY:  {"atk": 3, "def": 3, "mobility": 2},
    UnitType.CAVALRY:   {"atk": 5, "def": 2, "mobility": 5},
    UnitType.ARTILLERY: {"atk": 7, "def": 1, "mobility": 1},
}


@dataclass
class Unit:
    id: str
    owner_id: str
    type: str
    atk: float
    def_: float
    mobility: float
    max_mobility: float
    alive: bool
    hex_id: str

    @property
    def power_score(self) -> float:
        return self.atk + self.def_ + 0.5 * self.max_mobility

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "type": self.type,
            "atk": self.atk,
            "def": self.def_,
            "mobility": self.mobility,
            "max_mobility": self.max_mobility,
            "alive": self.alive,
            "hex_id": self.hex_id,
            "power_score": self.power_score,
        }

    @classmethod
    def create(cls, owner_id: str, unit_type: str, hex_id: str) -> "Unit":
        """Factory: create a unit from type stats."""
        stats = UNIT_STATS[unit_type]
        return cls(
            id=str(uuid.uuid4())[:8],
            owner_id=owner_id,
            type=unit_type,
            atk=stats["atk"],
            def_=stats["def"],
            mobility=stats["mobility"],
            max_mobility=stats["mobility"],
            alive=True,
            hex_id=hex_id,
        )


# ─── Hex / board ────────────────────────────────────────────────────────

@dataclass
class HexState:
    hex_id: str
    q: int
    r: int
    terrain_type: str = "plain"
    owner_id: str | None = None
    is_capital: bool = False
    resources: dict = field(default_factory=dict)

    def to_dict(self, visible: bool = False) -> dict:
        d: dict = {"hex_id": self.hex_id, "q": self.q, "r": self.r}
        if visible:
            d["terrain_type"] = self.terrain_type
            d["owner_id"] = self.owner_id
            d["is_capital"] = self.is_capital
            d["resources"] = self.resources
        else:
            d["fog_of_war"] = True
        return d


# ─── Diplomatic message ─────────────────────────────────────────────────

@dataclass
class Message:
    id_message: str
    from_player: str
    to_player: str
    text: str
    turn_sent: int
    delivered: bool
    word_count: int
    cosine_similarity: float = 0.0
    theme_similarity: float = 0.0


# ─── Player state ───────────────────────────────────────────────────────

@dataclass
class PlayerState:
    player_id: str
    food: float = 20.0
    gold: float = 10.0
    units: dict[str, Unit] = field(default_factory=dict)
    territories: set[str] = field(default_factory=set)
    semantic_score: float = 0.0
    theme_similarity: float = 0.0
    messages_sent_this_turn: int = 0
    last_message_text: str = ""
    messages_outbox: list[str] = field(default_factory=list)
    messages_inbox: list[str] = field(default_factory=list)
    afk_streak: int = 0
    is_eliminated: bool = False


# ─── Game event ─────────────────────────────────────────────────────────

EVENT_TYPES = [
    "MESSAGE_DELIVERED", "SEMANTIC_SCORE", "THEME_BONUS",
    "FAMINE_EVENT", "UNIT_KILLED", "UNIT_RECRUITED",
    "TERRITORY_CAPTURED", "COMBAT_RESOLVED",
    "TURN_STARTED", "TURN_ENDED", "VICTORY",
]
