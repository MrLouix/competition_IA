#!/usr/bin/env python3
"""
Empires in the Fog — Official MCP Server
=========================================
Server MCP conforme au Model Context Protocol pour le jeu
« Empires in the Fog ». Expose les outils de jeu via stdio.
"""

from __future__ import annotations

import math
import random
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from empires_in_the_fog.config import GameConfig, DEFAULT_BOARD_TEMPLATES
from empires_in_the_fog.models import (
    Unit,
    HexState,
    Message,
    PlayerState,
    UNIT_STATS,
    EVENT_TYPES,
)


# ─── Game engine ────────────────────────────────────────────────────────

class GameState:
    """Core game state — tracks players, board, turns, and events."""

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

    # ── Registration & init ─────────────────────────────────────────────

    def register_player(self, player_id: str) -> dict:
        if self._initialized:
            return {"error": "Game already initialized"}
        if player_id in self.players:
            return {"player_id": player_id, "status": "already_registered"}
        self.players[player_id] = PlayerState(player_id=player_id, food=20.0, gold=10.0)
        return {
            "player_id": player_id,
            "status": "registered",
            "total_players": len(self.players),
        }

    def initialize_game(self) -> dict:
        if len(self.players) < 2:
            return {"error": "Need at least 2 players to start"}

        # Build the board
        for tpl in DEFAULT_BOARD_TEMPLATES:
            self.board[tpl["hex_id"]] = HexState(
                hex_id=tpl["hex_id"],
                q=tpl["q"],
                r=tpl["r"],
                terrain_type=tpl["terrain"],
                is_capital=(tpl["terrain"] == "capital"),
            )

        # Assign opposite capitals
        plist = list(self.players.keys())
        self.players[plist[0]].territories.add("h0_0")
        self.board["h0_0"].owner_id = plist[0]

        opposite = "h0_-2"
        self.players[plist[1]].territories.add(opposite)
        self.board[opposite].owner_id = plist[1]
        self.board[opposite].terrain_type = "capital"
        self.board[opposite].is_capital = True

        # Starting units (3 scouts each)
        for pid in plist:
            cap = "h0_0" if pid == plist[0] else opposite
            for _ in range(3):
                u = Unit.create(owner_id=pid, unit_type="scout", hex_id=cap)
                self.players[pid].units[u.id] = u

        self.turn_order = plist
        self.current_turn = 1
        self.current_player_id = plist[0]
        self.turn_start_time = time.time()
        self._initialized = True

        self._log_event("TURN_STARTED", {
            "turn": self.current_turn,
            "player": self.current_player_id,
        })
        return {
            "status": "initialized",
            "players": plist,
            "current_turn": 1,
            "first_player": plist[0],
        }

    # ── Turn logic ──────────────────────────────────────────────────────

    def is_my_turn(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        if not self._initialized:
            return {
                "status": "not_initialized",
                "registered_players": list(self.players.keys()),
            }
        if self.winner:
            return {"game_over": True, "winner": self.winner}

        if player_id == self.current_player_id:
            ps = self.players[player_id]
            pending: list[dict] = []
            for other_pid, other_ps in self.players.items():
                if other_pid != player_id and other_ps.messages_outbox:
                    for txt in list(other_ps.messages_outbox):
                        pending.append({
                            "from": other_pid,
                            "text": txt,
                            "turn": self.current_turn - 1,
                        })
                    other_ps.messages_outbox.clear()

            elapsed = time.time() - (self.turn_start_time or time.time())
            return {
                "is_turn": True,
                "turn_number": self.current_turn,
                "food": round(ps.food, 2),
                "gold": round(ps.gold, 2),
                "active_units": len([u for u in ps.units.values() if u.alive]),
                "territories": len(ps.territories),
                "semantic_score": round(ps.semantic_score, 2),
                "time_limit": self.config.TIMEOUT_TURN_SECONDS,
                "elapsed_sec": round(elapsed, 1),
                "remaining_sec": round(
                    max(0, self.config.TIMEOUT_TURN_SECONDS - elapsed), 1
                ),
                "pending_messages": pending,
                "reference_message_for_similarity": self._opponent_messages.get(
                    player_id, self._initial_verse
                ),
                "global_theme": self.config.global_theme,
            }

        return {
            "is_turn": False,
            "current_player": self.current_player_id,
            "waiting": player_id,
        }

    def end_turn(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        if not self._initialized:
            return {"error": "Game not initialized"}
        if player_id != self.current_player_id:
            return {
                "error": "Not your turn",
                "current_player": self.current_player_id,
            }

        elapsed = time.time() - (self.turn_start_time or time.time())
        ps = self.players[player_id]
        active_units = len([u for u in ps.units.values() if u.alive])

        # Food cost = BASE_COST + (ATTRITION_RATE × units × duration / 60)
        turn_cost = self.config.BASE_COST + (
            self.config.ATTRITION_RATE * active_units * elapsed / 60
        )
        ps.food -= turn_cost

        result: dict = {
            "turn_number": self.current_turn,
            "turn_duration_sec": round(elapsed, 2),
            "food_consumed": round(turn_cost, 3),
        }

        # ── Famine check ────────────────────────────────────────────────
        if ps.food < 0:
            famine_result = self._resolve_famine(player_id, elapsed)
            result["famine_event"] = famine_result
        else:
            result["food_remaining"] = round(ps.food, 2)

        # ── Territory income ────────────────────────────────────────────
        income = 0
        for hid in ps.territories:
            hs = self.board.get(hid)
            if hs:
                income += self.config.terrain_income.get(hs.terrain_type, 0)
        ps.food += income
        ps.gold += income
        result["income"] = income
        result["food_after_income"] = round(ps.food, 2)

        # ── Semantic scoring ────────────────────────────────────────────
        if ps.last_message_text:
            ref = self._opponent_messages.get(player_id, self._initial_verse)
            sim = self._similarity(ps.last_message_text, ref)
            theme_sim = self._similarity(
                ps.last_message_text, self.config.global_theme
            )
            meets = sim >= self.config.SEMANTIC_THRESHOLD_ADJACENT
            pts = 0.0
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
                "points_earned": round(pts, 2),
                "meets_threshold": meets,
            }
            self._opponent_messages[player_id] = ps.last_message_text

        # Reset per-turn state
        ps.messages_sent_this_turn = 0
        ps.last_message_text = ""

        # Restore mobility
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

        # ── Elimination check ───────────────────────────────────────────
        for pid, pstate in self.players.items():
            if self._is_eliminated(pstate):
                pstate.is_eliminated = True
                others = [p for p in self.players if p != pid]
                if len(others) == 1:
                    self.winner = others[0]

        self._log_event("TURN_ENDED", {
            "player": player_id,
            "turn": self.current_turn,
        })

        result["next_turn"] = self.current_turn
        result["next_player"] = self.current_player_id
        result["game_over"] = self.winner is not None
        return result

    def _resolve_famine(self, player_id: str, elapsed: float) -> dict:
        """Resolve famine — kill weakest units to absorb deficit."""
        ps = self.players[player_id]
        deficit = -ps.food
        cost_per_unit = self.config.ATTRITION_RATE * (elapsed / 60)

        # Edge case: instant turn or rate=0
        if cost_per_unit <= 1e-9:
            ps.semantic_score = max(0, ps.semantic_score - 1)
            ps.food = 0
            return {
                "type": "moral_penalty",
                "semantic_penalty": -1,
                "no_attrition": True,
            }

        alive = [
            u
            for u in ps.units.values()
            if u.alive and u.type not in self.config.elite_unit_types
        ]
        if not alive:
            ps.food = 0
            return {"type": "no_units_alive", "food_set_to_zero": True}

        n_to_kill = min(
            math.ceil(deficit / cost_per_unit), len(alive)
        )

        alive.sort(key=lambda u: u.power_score)
        pool_size = max(1, len(alive) // 2)
        pool = alive[:pool_size]
        to_kill = random.sample(pool, min(n_to_kill, len(pool)))

        killed_units: list[dict] = []
        for u in to_kill:
            u.alive = False
            killed_units.append(u.to_dict())
            self._log_event("UNIT_KILLED", {
                "unit_id": u.id, "reason": "famine",
            })

        ps.food = 0
        return {
            "type": "famine",
            "deficit": round(deficit, 2),
            "cost_per_unit": round(cost_per_unit, 3),
            "units_killed": len(killed_units),
            "killed_units": killed_units,
            "food_set_to_zero": True,
            "remaining_food": 0,
        }

    def _is_eliminated(self, ps: PlayerState) -> bool:
        alive = any(u.alive for u in ps.units.values())
        return not alive and len(ps.territories) == 0 and ps.food <= 0

    # ── Actions ─────────────────────────────────────────────────────────

    def move_unit(
        self, player_id: str, unit_id: str, target_hex_id: str
    ) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        unit = ps.units.get(unit_id)
        if not unit or not unit.alive:
            return {"error": "Unit not found or dead"}
        if unit.owner_id != player_id:
            return {"error": "Not your unit"}
        if target_hex_id not in self.board:
            return {"error": "Invalid hex", "hex_id": target_hex_id}

        src_hex = self.board.get(unit.hex_id)
        dst_hex = self.board[target_hex_id]
        if not src_hex or not dst_hex:
            return {"error": "Hex not found"}

        # Axial distance
        dist = (
            abs(src_hex.q - dst_hex.q)
            + abs(src_hex.q + src_hex.r - dst_hex.q - dst_hex.r)
            + abs(src_hex.r - dst_hex.r)
        ) / 2
        if dist > self.config.fog_reveal_radius:
            return {"error": "Hex too far", "distance": dist}

        required_mobility = max(1, int(dist))
        if unit.mobility < required_mobility:
            return {
                "error": "Not enough mobility",
                "needed": required_mobility,
                "have": round(unit.mobility, 1),
            }

        # Check for enemy unit → combat
        for pid, other_ps in self.players.items():
            if pid == player_id:
                continue
            for ou in other_ps.units.values():
                if ou.alive and ou.hex_id == target_hex_id:
                    return self._resolve_combat(
                        unit, ou, player_id, pid, src_hex, required_mobility
                    )

        # Plain move
        old_hex = unit.hex_id
        unit.hex_id = target_hex_id
        unit.mobility -= required_mobility

        # Capture territory if uncontested
        self._maybe_capture(player_id, target_hex_id, old_hex)

        return {
            "success": True,
            "unit_id": unit_id,
            "from_hex": old_hex,
            "to_hex": target_hex_id,
            "remaining_mobility": round(unit.mobility, 1),
        }

    def _maybe_capture(
        self, player_id: str, target_hex_id: str, old_hex_id: str
    ) -> None:
        """Transfer territory ownership if no enemy presence."""
        ps = self.players[player_id]
        enemy_here = any(
            ou.alive and ou.hex_id == target_hex_id
            for pid, op in self.players.items()
            for ou in op.units.values()
            if pid != player_id
        )
        if not enemy_here:
            self.board[target_hex_id].owner_id = player_id
            ps.territories.add(target_hex_id)
            self._log_event("TERRITORY_CAPTURED", {
                "hex_id": target_hex_id,
                "capturer": player_id,
                "terrain": self.board[target_hex_id].terrain_type,
            })
            # Abandon old territory if no unit remains there
            still_here = any(
                u.alive and u.hex_id == old_hex_id for u in ps.units.values()
            )
            if not still_here:
                ps.territories.discard(old_hex_id)

    def _resolve_combat(
        self,
        attacker: Unit,
        defender: Unit,
        atk_player_id: str,
        def_player_id: str,
        src_hex: HexState,
        mobility_cost: int,
    ) -> dict:
        """Resolve a single-unit combat encounter."""
        terrain = (
            self.board.get(defender.hex_id).terrain_type
            if self.board.get(defender.hex_id)
            else "plain"
        )
        terrain_bonus_key = terrain if terrain in self.config.terrain_bonus else "plain"
        t_bonus = self.config.terrain_bonus[terrain_bonus_key]

        roll_atk = attacker.atk * random.uniform(0.8, 1.2)
        roll_def = defender.def_ * random.uniform(0.8, 1.2) * t_bonus

        event = {
            "attacker_id": attacker.id,
            "attacker_type": attacker.type,
            "defender_id": defender.id,
            "defender_type": defender.type,
            "roll_atk": round(roll_atk, 2),
            "roll_def": round(roll_def, 2),
            "terrain_bonus": t_bonus,
            "terrain": terrain,
        }

        # BUG FIX: save old_hex BEFORE any mutation in both branches
        old_hex = attacker.hex_id

        if roll_atk > roll_def:
            defender.alive = False
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

    def recruit_unit(
        self, player_id: str, unit_type: str, position_hex: str
    ) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        if unit_type not in self.config.unit_costs:
            return {
                "error": "Unknown unit type",
                "valid_types": list(self.config.unit_costs.keys()),
            }
        cost = self.config.unit_costs[unit_type]
        if ps.gold < cost:
            return {
                "error": "Not enough gold",
                "have": ps.gold,
                "need": cost,
            }
        if position_hex not in ps.territories:
            return {
                "error": "Hex not controlled",
                "hex_id": position_hex,
                "your_territories": sorted(ps.territories),
            }

        uid = str(uuid.uuid4())[:8]
        unit = Unit.create(
            owner_id=player_id, unit_type=unit_type, hex_id=position_hex
        )
        unit.id = uid
        ps.units[uid] = unit
        ps.gold -= cost

        self._log_event("UNIT_RECRUITED", {
            "unit_id": uid,
            "type": unit_type,
            "cost": cost,
            "position": position_hex,
            "player": player_id,
        })
        return {
            "success": True,
            "unit_id": uid,
            "type": unit_type,
            "gold_cost": cost,
            "gold_remaining": ps.gold,
        }

    # ── Diplomacy ───────────────────────────────────────────────────────

    def send_semantic_message(self, player_id: str, text: str) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        words = text.strip().split()

        # FIX: proper empty check
        if not words:
            return {"error": "EMPTY_MESSAGE"}

        if len(words) > self.config.MAX_MESSAGE_WORDS:
            return {
                "error": "MESSAGE_TOO_LONG",
                "word_count": len(words),
                "max_allowed": self.config.MAX_MESSAGE_WORDS,
            }
        if ps.messages_sent_this_turn >= self.config.MAX_MESSAGES_PER_TURN:
            return {
                "error": "ALREADY_SENT",
                "message": "Already sent a message this turn",
                "current_message": ps.last_message_text,
            }

        msg_id = str(uuid.uuid4())[:8]
        opponents = [p for p in self.players if p != player_id]
        target = opponents[0] if opponents else "server"

        Message(
            id_message=msg_id,
            from_player=player_id,
            to_player=target,
            text=text,
            turn_sent=self.current_turn,
            delivered=False,
            word_count=len(words),
        )
        # We append text to outbox (message object not stored, just text)
        ps.messages_outbox.append(text)
        ps.last_message_text = text
        ps.messages_sent_this_turn += 1

        return {
            "success": True,
            "message_id": msg_id,
            "word_count": len(words),
            "delivered_to": target,
        }

    def read_messages(
        self, player_id: str, turn: int | None = None
    ) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        msgs: list[dict] = []
        for t in ps.messages_inbox:
            msgs.append({"text": t, "type": "received"})
        for t in ps.messages_outbox:
            msgs.append({"text": t, "type": "pending_delivery"})
        if ps.last_message_text:
            msgs.append({"text": ps.last_message_text, "type": "sent_this_turn"})
        return {"messages": msgs, "player_id": player_id}

    # ── Visibility & state ──────────────────────────────────────────────

    def get_visible_map(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        hexes: list[dict] = []
        for hid, hs in self.board.items():
            if hid in ps.territories:
                hexes.append(hs.to_dict(visible=True))
            else:
                visible = False
                for th_id in ps.territories:
                    th = self.board.get(th_id)
                    if th:
                        dist = (
                            abs(th.q - hs.q)
                            + abs(th.q + th.r - hs.q - hs.r)
                            + abs(th.r - hs.r)
                        ) / 2
                        if dist <= self.config.fog_reveal_radius:
                            visible = True
                            break
                hexes.append(hs.to_dict(visible=visible))
        return {
            "hexes": hexes,
            "fog_reveal_radius": self.config.fog_reveal_radius,
            "player_territories": sorted(ps.territories),
        }

    def get_units(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        ps = self.players[player_id]
        alive = [u for u in ps.units.values() if u.alive]
        return {
            "units": [u.to_dict() for u in alive],
            "total": len(alive),
        }

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
                    "food": round(p.food, 2),
                    "gold": round(p.gold, 2),
                    "active_units": len(
                        [u for u in p.units.values() if u.alive]
                    ),
                    "territories": sorted(p.territories),
                    "semantic_score": round(p.semantic_score, 2),
                    "is_eliminated": p.is_eliminated,
                }
                for pid, p in self.players.items()
            },
        }

    def surrender(self, player_id: str) -> dict:
        self._ensure_player(player_id)
        opponents = [p for p in self.players if p != player_id]
        if opponents:
            self.winner = opponents[0]
        self._log_event("VICTORY", {
            "winner": self.winner,
            "surrendered_by": player_id,
        })
        return {
            "surrendered": True,
            "winner": self.winner,
            "game_over": True,
        }

    def get_game_rules(self) -> dict:
        """Return full game rules in markdown with current config values."""
        rules_path = Path(__file__).parent / "rules" / "game_rules.md"
        rules_path_alt = Path(__file__).parent.parent / "rules" / "game_rules.md"

        if rules_path.exists():
            content = rules_path.read_text(encoding="utf-8")
        elif rules_path_alt.exists():
            content = rules_path_alt.read_text(encoding="utf-8")
        else:
            content = self._generate_builtin_rules()

        config_block = self._config_to_markdown()
        content = content.replace(
            "## Configuration Defaults",
            f"## Configuration Defaults (partie: {self.game_id})\n"
            f"{config_block}\n"
            f"---\n"
            f"## Configuration Defaults (default values)",
        )
        return {
            "rules_markdown": content,
            "game_id": self.game_id,
            "config": self._config_to_dict(),
        }

    # ── Utilities ───────────────────────────────────────────────────────

    def _ensure_player(self, player_id: str) -> None:
        """Ensure a player is registered; auto-register if not."""
        if player_id not in self.players:
            self.players[player_id] = PlayerState(
                player_id=player_id, food=20.0, gold=10.0
            )

    def _log_event(self, event_type: str, data: dict) -> None:
        self.event_log.append({
            "event_type": event_type,
            "ts": time.time(),
            **data,
        })

    def _similarity(self, text_a: str, text_b: str) -> float:
        """Compute similarity between 0 and 1. Falls back gracefully."""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity

            vec = TfidfVectorizer()
            tfidf = vec.fit_transform([text_a, text_b])
            return float(cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0])
        except ImportError:
            pass

        # Jaccard fallback
        set_a = set(text_a.lower().split())
        set_b = set(text_b.lower().split())
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    def _config_to_markdown(self) -> str:
        c = self.config
        rows = [
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
        return "\n".join(rows)

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
        """Fallback rules text when markdown file is not found."""
        c = self.config
        return (
            f"# Empires in the Fog - Game Rules\n\n"
            f"## Turn System\n"
            f"- Timer: {c.TIMEOUT_TURN_SECONDS}s\n"
            f"- Food cost: BASE_COST({c.BASE_COST}) + ATTRITION_RATE({c.ATTRITION_RATE}) × units × duration/60\n\n"
            f"## Victory\n"
            f"- Domination or Score after {c.MAX_TURNS} turns\n\n"
            f"## Diplomacy (\u2264 {c.MAX_MESSAGE_WORDS} words)\n"
            f"- Adjacent threshold: {c.SEMANTIC_THRESHOLD_ADJACENT}\n"
            f"- Theme threshold: {c.SEMANTIC_THRESHOLD_THEME}\n"
            f"- Theme bonus: \u00d7{c.THEME_BONUS}\n\n"
            f"## Combat\n"
            f"- roll_atk = atk \u00d7 random(0.8, 1.2)\n"
            f"- roll_def = def \u00d7 random \u00d7 terrain_bonus\n\n"
            f"## Configuration\n{self._config_to_markdown()}\n"
        )


# ─── MCP server ─────────────────────────────────────────────────────────

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


# ─── MCP tools ──────────────────────────────────────────────────────────

@_mcp.tool()
def register_player(player_id: str, game_id: str = "default") -> dict:
    """Enregistrer un joueur dans la partie avant le début."""
    return _get_game(game_id).register_player(player_id)


@_mcp.tool()
def initialize_game(game_id: str = "default") -> dict:
    """Initialiser la partie (placer unités, territoires). Requiert \u22652 joueurs."""
    return _get_game(game_id).initialize_game()


@_mcp.tool()
def is_my_turn(player_id: str, game_id: str = "default") -> dict:
    """Vérifier si c'est le tour du joueur. Retourne infos du tour + messages."""
    return _get_game(game_id).is_my_turn(player_id)


@_mcp.tool()
def end_turn(player_id: str, game_id: str = "default") -> dict:
    """Terminer le tour du joueur. Calcule nourriture, attrition, scoring."""
    return _get_game(game_id).end_turn(player_id)


@_mcp.tool()
def move_unit(
    player_id: str, unit_id: str, target_hex: str, game_id: str = "default"
) -> dict:
    """Déplacer une unité vers un hex cible. Combat si ennemi présent."""
    return _get_game(game_id).move_unit(player_id, unit_id, target_hex)


@_mcp.tool()
def recruit_unit(
    player_id: str, unit_type: str, position_hex: str, game_id: str = "default"
) -> dict:
    """Recruter une unité sur un territoire contrôlé."""
    return _get_game(game_id).recruit_unit(player_id, unit_type, position_hex)


@_mcp.tool()
def send_semantic_message(
    player_id: str, text: str, game_id: str = "default"
) -> dict:
    """Envoyer un message diplomatique (max 10 mots)."""
    return _get_game(game_id).send_semantic_message(player_id, text)


@_mcp.tool()
def read_messages(
    player_id: str, game_id: str = "default"
) -> dict:
    """Lire les messages reçus et envoyés."""
    return _get_game(game_id).read_messages(player_id)


@_mcp.tool()
def get_visible_map(
    player_id: str, game_id: str = "default"
) -> dict:
    """Obtenir la carte visible avec brouillard de guerre."""
    return _get_game(game_id).get_visible_map(player_id)


@_mcp.tool()
def get_units(player_id: str, game_id: str = "default") -> dict:
    """Obtenir les unités du joueur."""
    return _get_game(game_id).get_units(player_id)


@_mcp.tool()
def get_full_state(
    spectator_token: str = "", game_id: str = "default"
) -> dict:
    """Obtenir l'état complet du jeu (spectateur)."""
    return _get_game(game_id).get_full_state()


@_mcp.tool()
def get_game_rules(game_id: str = "default") -> dict:
    """Récupérer les règles complètes du jeu en markdown."""
    return _get_game(game_id).get_game_rules()


@_mcp.tool()
def surrender(player_id: str, game_id: str = "default") -> dict:
    """Se rendre. L'adversaire gagne immédiatement."""
    return _get_game(game_id).surrender(player_id)


@_mcp.tool()
def reset_game(game_id: str = "default") -> dict:
    """Réinitialiser complètement la partie."""
    global _game_instances
    _game_instances[game_id] = GameState(game_id)
    return {"status": "reset", "game_id": game_id}


# ─── Entry point ────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Empires in the Fog MCP Server"
    )
    parser.add_argument(
        "--game-id", default="default", help="Game instance ID"
    )
    args = parser.parse_args()
    _get_game(args.game_id)
    _mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
