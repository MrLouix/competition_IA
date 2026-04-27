#!/usr/bin/env python3
"""
Empires in the Fog — Official MCP Server
=========================================
Serveur MCP conforme au Model Context Protocol (spec 2024-11) pour le jeu
"Empires in the Fog". Expose les outils de jeu via stdio.

Configuration Hermes (~/.hermes/config.yaml) :

    mcp_servers:
      empires-in-the-fog:
        command: "python3"
        args:
          - "/home/ai_agent/projects/competition_IA/empires_in_the_fog/server.py"
          - "--game-id"
          - "default"

Usage standalone :
    python3 -m empires_in_the_fog.server
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# ─── Configuration par défaut (v1.4) ────────────────────────────────────────

@dataclass
class GameConfig:
    MAX_MESSAGES_PER_TURN: int = 1
    MAX_MESSAGE_WORDS: int = 10
    TIMEOUT_TURN_SECONDS: int = 60
    BASE_COST: float = 0.5
    ATTRITION_RATE: float = 0.1          # nourriture / unité / minute
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


# ─── Modèles de données ─────────────────────────────────────────────────────

class UnitType:
    SCOUT = "scout"
    INFANTRY = "infantry"
    CAVALRY = "cavalry"
    ARTILLERY = "artillery"


UNIT_STATS: dict[str, dict[str, float]] = {
    UnitType.SCOUT:    {"atk": 1, "def": 1, "mobility": 4},
    UnitType.INFANTRY: {"atk": 3, "def": 3, "mobility": 2},
    UnitType.CAVALRY:  {"atk": 5, "def": 2, "mobility": 5},
    UnitType.ARTILLERY:{"atk": 7, "def": 1, "mobility": 1},
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
            "id": self.id, "owner_id": self.owner_id, "type": self.type,
            "atk": self.atk, "def": self.def_, "mobility": self.mobility,
            "max_mobility": self.max_mobility, "alive": self.alive,
            "hex_id": self.hex_id, "power_score": self.power_score,
        }


@dataclass
class HexState:
    hex_id: str
    q: int
    r: int
    terrain_type: str = "plain"
    owner_id: str | None = None
    is_capital: bool = False
    resources: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, visible: bool = False) -> dict:
        d = {"hex_id": self.hex_id, "q": self.q, "r": self.r}
        if visible:
            d["terrain_type"] = self.terrain_type
            d["owner_id"] = self.owner_id
            d["resources"] = self.resources
        else:
            d["fog_of_war"] = True
        return d


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


@dataclass
class PlayerState:
    player_id: str
    food: float
    gold: float
    units: dict[str, Unit] = field(default_factory=dict)
    territories: set[str] = field(default_factory=set)
    semantic_score: float = 0
    theme_similarity: float = 0
    messages_sent_this_turn: int = 0
    last_message_text: str = ""
    messages_outbox: list[str] = field(default_factory=list)
    messages_inbox: list[str] = field(default_factory=list)
    afk_streak: int = 0
    is_eliminated: bool = False


# ─── Plateau par défaut (petite carte 7 hex) ────────────────────────────────

DEFAULT_BOARD_TEMPLATES: list[dict] = [
    {"hex_id": "h0_0", "q": 0, "r": 0,  "terrain": "capital"},
    {"hex_id": "h1_0", "q": 1, "r": 0,  "terrain": "plain"},
    {"hex_id": "h-1_0", "q": -1, "r": 0, "terrain": "forest"},
    {"hex_id": "h0_1", "q": 0, "r": 1,  "terrain": "plain"},
    {"hex_id": "h0_-1", "q": 0, "r": -1, "terrain": "plain"},
    {"hex_id": "h1_-1", "q": 1, "r": -1, "terrain": "village"},
    {"hex_id": "h-1_1", "q": -1, "r": 1, "terrain": "village"},
    {"hex_id": "h1_1", "q": 1, "r": 1,  "terrain": "forest"},
    {"hex_id": "h-1_-1", "q": -1, "r": -1, "terrain": "plain"},
    {"hex_id": "h0_2", "q": 0, "r": 2,  "terrain": "city"},
    {"hex_id": "h0_-2", "q": 0, "r": -2, "terrain": "city"},
    {"hex_id": "h2_-1", "q": 2, "r": -1, "terrain": "plain"},
    {"hex_id": "h-2_1", "q": -2, "r": 1, "terrain": "plain"},
]

# ─── Moteur de jeu ──────────────────────────────────────────────────────────

class GameState:
    def __init__(self, game_id: str, config: GameConfig | None = None):
        self.game_id = game_id
        self.config = config or GameConfig()
        self.players: dict[str, PlayerState] = {}
        self.board: dict[str, HexState] = {}
        self.turn_order: list[str] = []
        self.current_turn: int = 0
        self.current_player_id: str | None = None
        self.turn_start_time: float | None = None
        self.winner: str | None = None
        self.event_log: list[dict] = []
        self._initialized = False

        self._initial_verse = self.config.initial_verse
        self._opponent_messages: dict[str, str] = {}  # player_id -> last message

    # --- Init ---
    def register_player(self, player_id: str) -> dict:
        if self._initialized:
            return {"error": "Game already initialized"}
        if player_id in self.players:
            return {"player_id": player_id, "status": "already_registered"}
        self.players[player_id] = PlayerState(player_id=player_id, food=20.0, gold=10.0)
        return {"player_id": player_id, "status": "registered", "total_players": len(self.players)}

    def initialize_game(self) -> dict:
        if len(self.players) < 2:
            return {"error": "Need at least 2 players to start"}

        # Board
        for t in DEFAULT_BOARD_TEMPLATES:
            self.board[t["hex_id"]] = HexState(
                hex_id=t["hex_id"], q=t["q"], r=t["r"], terrain_type=t["terrain"],
                is_capital=(t["terrain"] == "capital"),
            )

        # Assign capitals
        plist = list(self.players.keys())
        self.players[plist[0]].territories.add("h0_0")
        self.board["h0_0"].owner_id = plist[0]

        # Find opposite hex for P2 capital
        opposite = "h0_-2"
        self.players[plist[1]].territories.add(opposite)
        self.board[opposite].owner_id = plist[1]
        self.board[opposite].terrain_type = "capital"
        self.board[opposite].is_capital = True

        # Starting units
        for pid in plist:
            for i in range(3):
                uid = str(uuid.uuid4())[:8]
                unit = Unit(id=uid, owner_id=pid, type="scout", atk=1, def=1,
                            mobility=4, max_mobility=4, alive=True,
                            hex_id="h0_0" if pid == plist[0] else opposite)
                self.players[pid].units[uid] = unit

        self.turn_order = plist
        self.current_turn = 1
        self.current_player_id = plist[0]
        self.turn_start_time = time.time()
        self._initialized = True

        self._log_event("TURN_STARTED", {
            "turn": self.current_turn, "player": self.current_player_id
        })
        return {"status": "initialized",
                "players": list(self.players.keys()),
                "current_turn": 1,
                "first_player": plist[0]}

    # --- Tour ---
    def is_my_turn(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        if not self._initialized:
            return {"status": "not_initialized", "registered_players": list(self.players.keys())}
        if self.winner:
            return {"game_over": True, "winner": self.winner}
        if player_id == self.current_player_id:
            pending = []
            # deliver outbox messages from opponent
            for pid, ps in self.players.items():
                if pid != player_id and ps.messages_outbox:
                    for msg_text in ps.messages_outbox:
                        pending.append({"from": pid, "text": msg_text, "turn": self.current_turn - 1})
                    ps.messages_outbox.clear()

            elapsed = time.time() - (self.turn_start_time or time.time())
            return {
                "is_turn": True,
                "turn_number": self.current_turn,
                "food": self.players[player_id].food,
                "gold": self.players[player_id].gold,
                "active_units": len([u for u in self.players[player_id].units.values() if u.alive]),
                "territories": len(self.players[player_id].territories),
                "semantic_score": self.players[player_id].semantic_score,
                "time_limit": self.config.TIMEOUT_TURN_SECONDS,
                "elapsed_sec": round(elapsed, 1),
                "remaining_sec": round(max(0, self.config.TIMEOUT_TURN_SECONDS - elapsed), 1),
                "pending_messages": pending,
                "reference_message_for_similarity": self._opponent_messages.get(player_id, self._initial_verse),
                "global_theme": self.config.global_theme,
            }
        return {"is_turn": False, "current_player": self.current_player_id,
                "waiting": player_id}

    def end_turn(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        if not self._initialized:
            return {"error": "Game not initialized"}
        if player_id != self.current_player_id:
            return {"error": "Not your turn", "current_player": self.current_player_id}

        elapsed = time.time() - (self.turn_start_time or time.time())
        ps = self.players[player_id]
        active_units = len([u for u in ps.units.values() if u.alive])

        # Calculate food cost: BASE_COST + (ATTRITION_RATE × nb_unités × durée_sec / 60)
        turn_cost = self.config.BASE_COST + (self.config.ATTRITION_RATE * active_units * elapsed / 60)
        ps.food -= turn_cost

        result = {
            "turn_number": self.current_turn,
            "turn_duration_sec": round(elapsed, 2),
            "food_consumed": round(turn_cost, 3),
        }

        # Famine check
        if ps.food < 0:
            famine_result = self._resolve_famine(player_id, elapsed)
            result["famine_event"] = famine_result
        else:
            result["food_remaining"] = round(ps.food, 2)

        # Income from territories
        income = 0
        for hid in ps.territories:
            hex_state = self.board.get(hid)
            if hex_state:
                income += self.config.terrain_income.get(hex_state.terrain_type, 0)
        ps.food += income
        ps.gold += income  # gold income = food income for simplicity
        result["income"] = income
        result["food_after_income"] = round(ps.food, 2)

        # Semantic scoring
        if ps.last_message_text:
            ref = self._opponent_messages.get(player_id, self._initial_verse)
            sim = self._simple_similarity(ps.last_message_text, ref)
            theme_sim = self._simple_similarity(ps.last_message_text, self.config.global_theme)
            meets = sim >= self.config.SEMANTIC_THRESHOLD_ADJACENT
            if meets:
                pts = 1.0
                if theme_sim >= self.config.SEMANTIC_THRESHOLD_THEME:
                    pts *= self.config.THEME_BONUS
                ps.semantic_score += pts
            result["semantic_result"] = {
                "your_message": ps.last_message_text,
                "reference_message": ref,
                "cosine_similarity": round(sim, 4),
                "theme_similarity": round(theme_sim, 4),
                "points_earned": round(pts if meets else 0, 2),
                "meets_threshold": meets,
            }
            self._opponent_messages[player_id] = ps.last_message_text

        # Reset turn messages sent
        ps.messages_sent_this_turn = 0
        ps.last_message_text = ""

        # Reset unit mobility
        for u in ps.units.values():
            if u.alive:
                u.mobility = u.max_mobility

        # Advance turn
        idx = self.turn_order.index(player_id)
        next_idx = (idx + 1) % len(self.turn_order)
        if next_idx <= idx:
            self.current_turn += 1

        self.current_player_id = self.turn_order[next_idx]
        self.turn_start_time = time.time()

        # Check elimination
        for pid, pstate in self.players.items():
            if self._is_eliminated(pstate):
                pstate.is_eliminated = True
                others = [p for p in self.players if p != pid]
                if len(others) == 1:
                    self.winner = others[0]

        self._log_event("TURN_ENDED", {"player": player_id, "turn": self.current_turn})

        result["next_turn"] = self.current_turn
        result["next_player"] = self.current_player_id
        result["game_over"] = self.winner is not None
        return result

    def _resolve_famine(self, player_id: str, elapsed: float) -> dict:
        ps = self.players[player_id]
        deficit = -ps.food
        cost_per_unit = self.config.ATTRITION_RATE * (elapsed / 60)

        if cost_per_unit == 0:
            ps.semantic_score = max(0, ps.semantic_score - 1)
            ps.food = 0
            return {"type": "moral_penalty", "semantic_penalty": -1, "no_attrition": True}

        alive_units = [u for u in ps.units.values() if u.alive and u.type not in self.config.elite_unit_types]
        if not alive_units:
            ps.food = 0
            return {"type": "no_units_alive", "food_set_to_zero": True}

        n_to_kill = min(math.ceil(deficit / max(1e-6, cost_per_unit)), len(alive_units))

        alive_units.sort(key=lambda u: u.power_score)
        pool_size = max(1, len(alive_units) // 2)
        pool = alive_units[:pool_size]
        to_kill = random.sample(pool, min(n_to_kill, len(pool)))

        killed = []
        for u in to_kill:
            u.alive = False
            killed.append(u.to_dict())
            ps.messages_inbox.append(f"Unité {u.id} ({u.type}) morte de famine")
            self._log_event("UNIT_KILLED", {"unit_id": u.id, "reason": "famine"})

        ps.food = 0

        return {
            "type": "famine",
            "deficit": round(deficit, 2),
            "cost_per_unit": round(cost_per_unit, 3),
            "units_killed": len(killed),
            "killed_units": killed,
            "food_set_to_zero": True,
            "remaining_food": 0,
        }

    def _is_eliminated(self, ps: PlayerState) -> bool:
        alive = any(u.alive for u in ps.units.values())
        return not alive and len(ps.territories) == 0 and ps.food <= 0

    # --- Actions ---
    def move_unit(self, player_id: str, unit_id: str, target_hex: str) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        unit = ps.units.get(unit_id)
        if not unit or not unit.alive:
            return {"error": "Unit not found or dead"}
        if unit.owner_id != player_id:
            return {"error": "Not your unit"}
        if target_hex not in self.board:
            return {"error": "Invalid hex", "hex_id": target_hex}

        # Check adjacency (simplified axial distance ≤ 2)
        src_hex = self.board.get(unit.hex_id)
        dst_hex = self.board[target_hex]
        if not src_hex or not dst_hex:
            return {"error": "Hex not found"}
        dist = (abs(src_hex.q - dst_hex.q) + abs(src_hex.q + src_hex.r - dst_hex.q - dst_hex.r) + abs(src_hex.r - dst_hex.r)) / 2
        if dist > 2:
            return {"error": "Hex too far (max reveal radius)", "distance": dist}

        required_mobility = max(1, dist)
        if unit.mobility < required_mobility:
            return {"error": "Not enough mobility",
                    "needed": required_mobility, "have": unit.mobility}

        # Check for enemy unit → combat
        for pid, other_ps in self.players.items():
            if pid == player_id:
                continue
            for ou in other_ps.units.values():
                if ou.alive and ou.hex_id == target_hex:
                    return self._resolve_combat(unit, ou, player_id, pid, src_hex, required_mobility)

        # Move
        old_hex = unit.hex_id
        unit.hex_id = target_hex
        unit.mobility -= required_mobility

        # Capture territory if no enemy units
        enemy_here = False
        for pid, other_ps in self.players.items():
            if pid != player_id:
                for ou in other_ps.units.values():
                    if ou.alive and ou.hex_id == target_hex:
                        enemy_here = True
                        break
        if not enemy_here:
            self.board[target_hex].owner_id = player_id
            if target_hex not in ps.territories:
                ps.territories.add(target_hex)
                self._log_event("TERRITORY_CAPTURED", {
                    "hex_id": target_hex, "capturer": player_id,
                    "terrain": self.board[target_hex].terrain_type,
                })
            if old_hex in ps.territories:
                # Keep old territory if still have units there
                still_here = any(u.alive and u.hex_id == old_hex for u in ps.units.values())
                if still_here:
                    pass  # keep
                else:
                    ps.territories.discard(old_hex)

        return {
            "success": True,
            "unit_id": unit_id,
            "from_hex": old_hex,
            "to_hex": target_hex,
            "remaining_mobility": round(unit.mobility, 1),
        }

    def _resolve_combat(self, attacker: Unit, defender: Unit,
                        atk_player_id: str, def_player_id: str,
                        src_hex: HexState, mobility_cost: int) -> dict:
        from random import uniform
        terrain = self.board.get(defender.hex_id).terrain_type if self.board.get(defender.hex_id) else "plain"
        t_bonus = self.config.terrain_bonus.get(terrain, 1.0)

        roll_atk = attacker.atk * uniform(0.8, 1.2)
        roll_def = defender.def_ * uniform(0.8, 1.2) * t_bonus

        event = {
            "attacker_id": attacker.id, "attacker_type": attacker.type,
            "defender_id": defender.id, "defender_type": defender.type,
            "roll_atk": round(roll_atk, 2), "roll_def": round(roll_def, 2),
            "terrain_bonus": t_bonus, "terrain": terrain,
        }

        if roll_atk > roll_def:
            defender.alive = False
            old_hex = attacker.hex_id
            attacker.hex_id = defender.hex_id
            attacker.mobility = max(0, attacker.mobility - mobility_cost)
            self.board[attacker.hex_id].owner_id = atk_player_id
            atk_ps = self.players[atk_player_id]
            if attacker.hex_id not in atk_ps.territories:
                atk_ps.territories.add(attacker.hex_id)

            self.players[def_player_id].territories.discard(defender.hex_id)

            event["outcome"] = "attacker_wins"
            event["defender_killed"] = True
            event["territory_captured"] = attacker.hex_id
        else:
            attacker.mobility = max(0, attacker.mobility - 1)
            event["outcome"] = "repelled"
            event["attacker_repelled_to"] = old_hex

        self._log_event("COMBAT_RESOLVED", event)
        return {
            "combat": True,
            **event,
            "attacker_remaining_mobility": round(attacker.mobility, 1),
        }

    def recruit_unit(self, player_id: str, unit_type: str, position_hex: str) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        if unit_type not in self.config.unit_costs:
            return {"error": "Unknown unit type", "valid_types": list(self.config.unit_costs.keys())}
        cost = self.config.unit_costs[unit_type]
        if ps.gold < cost:
            return {"error": "Not enough gold", "have": ps.gold, "need": cost}
        if position_hex not in ps.territories:
            return {"error": "Hex not controlled", "hex_id": position_hex,
                    "your_territories": sorted(ps.territories)}

        stats = UNIT_STATS.get(unit_type)
        uid = str(uuid.uuid4())[:8]
        unit = Unit(id=uid, owner_id=player_id, type=unit_type,
                    atk=stats["atk"], def_=stats["def"],
                    mobility=stats["mobility"], max_mobility=stats["mobility"],
                    alive=True, hex_id=position_hex)
        ps.units[uid] = unit
        ps.gold -= cost

        self._log_event("UNIT_RECRUITED", {
            "unit_id": uid, "type": unit_type, "cost": cost,
            "position": position_hex, "player": player_id,
        })
        return {"success": True, "unit_id": uid, "type": unit_type,
                "gold_cost": cost, "gold_remaining": ps.gold}

    # --- Diplomatie sémantique ---
    def send_semantic_message(self, player_id: str, text: str) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        words = text.strip().split()

        if not words or (len(words) == 1 and not words[0].strip()):
            return {"error": "EMPTY_MESSAGE"}
        if len(words) > self.config.MAX_MESSAGE_WORDS:
            return {"error": "MESSAGE_TOO_LONG",
                    "word_count": len(words),
                    "max_allowed": self.config.MAX_MESSAGE_WORDS}
        if ps.messages_sent_this_turn >= self.config.MAX_MESSAGES_PER_TURN:
            return {"error": "ALREADY_SENT",
                    "message": "Already sent a message this turn",
                    "current_message": ps.last_message_text}

        msg_id = str(uuid.uuid4())[:8]

        # Find opponent
        opponents = [pid for pid in self.players if pid != player_id]
        target = opponents[0] if opponents else "server"

        msg = Message(
            id_message=msg_id, from_player=player_id, to_player=target,
            text=text, turn_sent=self.current_turn, delivered=False,
            word_count=len(words),
        )
        ps.messages_outbox.append(msg.text)
        ps.last_message_text = text
        ps.messages_sent_this_turn += 1

        return {"success": True, "message_id": msg_id, "word_count": msg.word_count,
                "delivered_to": target}

    def read_messages(self, player_id: str, turn: int | None = None) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        msgs = []
        for text in ps.messages_inbox:
            msgs.append({"text": text, "type": "received"})
        for text in ps.messages_outbox:
            msgs.append({"text": text, "type": "pending_delivery"})
        if ps.last_message_text:
            msgs.append({"text": ps.last_message_text, "type": "sent_this_turn"})
        return {"messages": msgs, "player_id": player_id}

    # --- Map & State ---
    def get_visible_map(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        hexes = []
        for hid, hs in self.board.items():
            if hid in ps.territories:
                hexes.append(hs.to_dict(visible=True))
            else:
                # Check distance to controlled hexes
                visible = False
                for territory_hex in ps.territories:
                    th = self.board.get(territory_hex)
                    if th:
                        dist = (abs(th.q - hs.q) + abs(th.q + th.r - hs.q - hs.r) + abs(th.r - hs.r)) / 2
                        if dist <= self.config.fog_reveal_radius:
                            visible = True
                            break
                hexes.append(hs.to_dict(visible=visible))
        return {"hexes": hexes, "fog_reveal_radius": self.config.fog_reveal_radius,
                "player_territories": sorted(ps.territories)}

    def get_units(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        return {"units": [u.to_dict() for u in ps.units.values() if u.alive],
                "total": len([u for u in ps.units.values() if u.alive])}

    def get_full_state(self) -> dict:
        if not self._initialized:
            return {"error": "Game not initialized"}
        return {
            "game_id": self.game_id,
            "current_turn": self.current_turn,
            "current_player": self.current_player_id,
            "winner": self.winner,
            "global_theme": self.config.global_theme,
            "players": {
                pid: {
                    "food": round(ps.food, 2),
                    "gold": round(ps.gold, 2),
                    "active_units": len([u for u in ps.units.values() if u.alive]),
                    "territories": sorted(ps.territories),
                    "semantic_score": round(ps.semantic_score, 2),
                    "is_eliminated": ps.is_eliminated,
                }
                for pid, ps in self.players.items()
            },
        }

    def surrender(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        opponents = [p for p in self.players if p != player_id]
        if opponents:
            self.winner = opponents[0]
        self._log_event("VICTORY", {"winner": self.winner, "surrendered_by": player_id})
        return {"surrendered": True, "winner": self.winner, "game_over": True}

    # --- Get rules ---
    def get_game_rules(self) -> dict:
        """Renvoi des règles complètes du jeu en markdown."""
        rules_path = Path(__file__).parent / "rules" / "game_rules.md"
        rules_path_alt = Path(__file__).parent.parent / "rules" / "game_rules.md"
        content = ""
        if rules_path.exists():
            content = rules_path.read_text(encoding="utf-8")
        elif rules_path_alt.exists():
            content = rules_path_alt.read_text(encoding="utf-8")
        else:
            content = self._generate_builtin_rules()

        # Injecter les valeurs actuelles de la config
        config_block = self._config_to_markdown()
        content = content.replace(
            "## Configuration Defaults",
            f"## Configuration Defaults (partie: {self.game_id})\n{config_block}\n---\n## Configuration Defaults (default values)"
        )
        return {"rules_markdown": content, "game_id": self.game_id,
                "config": self._config_to_dict()}

    # --- Utilitaires ---
    def _ensure_player(self, player_id: str):
        if player_id not in self.players:
            register = self.register_player(player_id)
        if player_id not in self.players:
            raise ValueError(f"Player {player_id} not registered")

    def _log_event(self, event_type: str, data: dict):
        self.event_log.append({"event_type": event_type, "ts": time.time(), **data})

    def _simple_similarity(self, text_a: str, text_b: str) -> float:
        """Similarité approximative sans embeddings (hash-based word overlap).
        En production, remplacer par all-MiniLM-L6-v2 via HuggingFace."""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
            vectorizer = TfidfVectorizer()
            tfidf = vectorizer.fit_transform([text_a, text_b])
            return float(cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0])
        except ImportError:
            # Fallback: simple Jaccard
            set_a = set(text_a.lower().split())
            set_b = set(text_b.lower().split())
            if not set_a or not set_b:
                return 0.0
            intersection = set_a & set_b
            union = set_a | set_b
            return len(intersection) / len(union)

    def _config_to_markdown(self) -> str:
        c = self.config
        lines = [
            "| Parameter | Value |",
            "|-----------|-------|",
            f"| `BASE_COST` | {c.BASE_COST} |",
            f"| `ATTRITION_RATE` | {c.ATTRITION_RATE} |",
            f"| `TIMEOUT_TURN_SECONDS` | {c.TIMEOUT_TURN_SECONDS} |",
            f"| `MAX_MESSAGES_PER_TURN` | {c.MAX_MESSAGES_PER_TURN} |",
            f"| `MAX_MESSAGE_WORDS` | {c.MAX_MESSAGE_WORDS} |",
            f"| `SEMANTIC_THRESHOLD_ADJACENT` | {c.SEMANTIC_THRESHOLD_ADJACENT} |",
            f"| `SEMANTIC_THRESHOLD_THEME` | {c.SEMANTIC_THRESHOLD_THEME} |",
            f"| `THEME_BONUS` | {c.THEME_BONUS} |",
            f"| `MAX_TURNS` | {c.MAX_TURNS} |",
            f"| `victory_condition` | {c.victory_condition} |",
            f"| `fog_reveal_radius` | {c.fog_reveal_radius} |",
            f"| `global_theme` | {c.global_theme} |",
            f"| `unit_costs` | {c.unit_costs} |",
            f"| `terrain_income` | {c.terrain_income} |",
        ]
        return "\n".join(lines)

    def _config_to_dict(self) -> dict:
        return {
            "BASE_COST": self.config.BASE_COST,
            "ATTRITION_RATE": self.config.ATTRITION_RATE,
            "TIMEOUT_TURN_SECONDS": self.config.TIMEOUT_TURN_SECONDS,
            "MAX_MESSAGES_PER_TURN": self.config.MAX_MESSAGES_PER_TURN,
            "MAX_MESSAGE_WORDS": self.config.MAX_MESSAGE_WORDS,
            "SEMANTIC_THRESHOLD_ADJACENT": self.config.SEMANTIC_THRESHOLD_ADJACENT,
            "SEMANTIC_THRESHOLD_THEME": self.config.SEMANTIC_THRESHOLD_THEME,
            "THEME_BONUS": self.config.THEME_BONUS,
            "MAX_TURNS": self.config.MAX_TURNS,
            "victory_condition": self.config.victory_condition,
            "fog_reveal_radius": self.config.fog_reveal_radius,
            "global_theme": self.config.global_theme,
            "unit_costs": self.config.unit_costs,
            "terrain_income": self.config.terrain_income,
        }

    def _generate_builtin_rules(self) -> str:
        """Génère les règles builtin quand le fichier markdown n'est pas trouvé."""
        c = self.config
        return f"""# Empires in the Fog - Game Rules

## Turn System
- Timer: {c.TIMEOUT_TURN_SECONDS}s, forced end on timeout
- Food cost: BASE_COST({c.BASE_COST}) + ATTRITION_RATE({c.ATTRITION_RATE}) × units × duration/60

## Victory
- Domination (eliminate opponent) or Score after {c.MAX_TURNS} turns
- Score = territories×3 + units×1 + semantic×5 + gold×0.5

## Diplomacy (Semantic, ≤ {c.MAX_MESSAGE_WORDS} words)
- Similarity threshold: {c.SEMANTIC_THRESHOLD_ADJACENT} (adjacent), {c.SEMANTIC_THRESHOLD_THEME} (theme)
- Theme bonus: ×{c.THEME_BONUS}

## Combat
- roll_atk = atk × random(0.8, 1.2)
- roll_def = def × random(0.8, 1.2) × terrain_bonus
- Terrain: fort/city 1.5×, forest 1.2×, plain 1.0×, open 0.8×

## Units
- Unit costs (gold): {c.unit_costs}
- Power = atk + def + 0.5 × mobility

## Configuration
{self._config_to_markdown()}
"""


# ─── Serveur MCP ─────────────────────────────────────────────────────────────

# State global (une instance par process)
_game_instances: dict[str, GameState] = {}

_mcp = FastMCP(
    "Empires in the Fog",
    instructions=(
        "Empires in the Fog — serveur de jeu de stratégie territoriale par IA. "
        "Commencer par s'enregistrer avec register_player, puis initialiser la partie "
        "avec initialize_game. Appeler get_game_rules pour connaître les règles."
    ),
)


def _get_game(game_id: str = "default") -> GameState:
    if game_id not in _game_instances:
        _game_instances[game_id] = GameState(game_id)
    return _game_instances[game_id]


# -- Outils MCP --

@_mcp.tool()
def register_player(player_id: str, game_id: str = "default") -> dict:
    """Enregistrer un joueur dans la partie. Appelable par les IA avant le début du jeu."""
    return _get_game(game_id).register_player(player_id)


@_mcp.tool()
def initialize_game(game_id: str = "default") -> dict:
    """Initialiser la partie (placer unités, territoires). Requiert ≥2 joueurs enregistrés."""
    return _get_game(game_id).initialize_game()


@_mcp.tool()
def is_my_turn(player_id: str, game_id: str = "default") -> dict:
    """Vérifier si c'est le tour du joueur. Retourne les infos du tour + messages en attente."""
    return _get_game(game_id).is_my_turn(player_id)


@_mcp.tool()
def end_turn(player_id: str, game_id: str = "default") -> dict:
    """Terminer le tour du joueur. Calcule le coût de nourriture, l'attrition, le scoring sémantique."""
    return _get_game(game_id).end_turn(player_id)


@_mcp.tool()
def move_unit(player_id: str, unit_id: str, target_hex: str, game_id: str = "default") -> dict:
    """Déplacer une unité vers un hex cible. Déclenche un combat si ennemi présent."""
    return _get_game(game_id).move_unit(player_id, unit_id, target_hex)


@_mcp.tool()
def recruit_unit(player_id: str, unit_type: str, position_hex: str, game_id: str = "default") -> dict:
    """Recruter une unité sur un territoire contrôlé. Coût en or selon le type."""
    return _get_game(game_id).recruit_unit(player_id, unit_type, position_hex)


@_mcp.tool()
def send_semantic_message(player_id: str, text: str, game_id: str = "default") -> dict:
    """Envoyer un message diplomatique (max 10 mots) à l'adversaire."""
    return _get_game(game_id).send_semantic_message(player_id, text)


@_mcp.tool()
def read_messages(player_id: str, game_id: str = "default") -> dict:
    """Lire les messages reçus et envoyés."""
    return _get_game(game_id).read_messages(player_id)


@_mcp.tool()
def get_visible_map(player_id: str, game_id: str = "default") -> dict:
    """Obtenir la carte visible (hexes contrôlés + brouillard de guerre, rayon 2)."""
    return _get_game(game_id).get_visible_map(player_id)


@_mcp.tool()
def get_units(player_id: str, game_id: str = "default") -> dict:
    """Obtenir les unités du joueur (uniquement les siennes)."""
    return _get_game(game_id).get_units(player_id)


@_mcp.tool()
def get_full_state(spectator_token: str = "", game_id: str = "default") -> dict:
    """Obtenir l'état complet du jeu (tous joueurs, unités, scores). Réservé au spectateur."""
    return _get_game(game_id).get_full_state()


@_mcp.tool()
def get_game_rules(game_id: str = "default") -> dict:
    """Récupérer les règles complètes du jeu en markdown lisible par une IA, incluant les valeurs de configuration actuelles."""
    return _get_game(game_id).get_game_rules()


@_mcp.tool()
def surrender(player_id: str, game_id: str = "default") -> dict:
    """Se rendre. L'adversaire gagne immédiatement."""
    return _get_game(game_id).surrender(player_id)


@_mcp.tool()
def reset_game(game_id: str = "default") -> dict:
    """Réinitialiser complètement la partie. Supprime tous les joueurs et l'état du jeu."""
    global _game_instances
    _game_instances[game_id] = GameState(game_id)
    return {"status": "reset", "game_id": game_id}


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Empires in the Fog MCP Server")
    parser.add_argument("--game-id", default="default", help="Game instance ID")
    args = parser.parse_args()

    # Pré-init avec l'ID de game
    _get_game(args.game_id)

    _mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
