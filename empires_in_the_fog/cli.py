#!/usr/bin/env python3
"""Empires in the Fog -- CLI orchestrator for running and managing games."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time

from empires_in_the_fog.server import GameState, _get_game, _game_instances


# Pre-built thematic messages for auto-play
_MESSAGES = [
    "peace through strength always",
    "alliance and unity forever",
    "war approaches from the fog",
    "trust no one in the shadows",
    "the empire strikes at dawn",
    "victory or death for our people",
    "honor and glory in battle",
    "the fog hides our true power",
    "stand together or fall alone",
    "might makes right in this world",
]


def cmd_run(args: argparse.Namespace) -> None:
    """Run a game in headless mode with auto-play."""
    game = _get_game(args.game_id)

    # Register players
    for name in args.players:
        r = game.register_player(name)
        print(f"  Register {name}: {r.get('status', r.get('error', '?'))}")

    # Initialize
    init = game.initialize_game()
    if "error" in init:
        print(f"Error: {init['error']}")
        sys.exit(1)
    print(f"\nGame initialized: {init['players']}")
    print(f"First player: {init['first_player']}")
    print("-" * 50)

    turn = 1
    max_turns = game.config.MAX_TURNS * len(game.players) + 5

    while turn <= max_turns:
        cp = game.current_player_id
        if not cp:
            break

        # Check turn availability
        info = game.is_my_turn(cp)
        if info.get("game_over"):
            print(f"\nGAME OVER -- Winner: {info.get('winner')}")
            break
        if not info.get("is_turn"):
            break

        # Pending messages
        if info.get("pending_messages"):
            for pm in info["pending_messages"]:
                print(f"  [{pm['from']}] \"{pm['text']}\"")

        # Auto-play: move first unit
        ps = game.players[cp]
        alive = [u for u in ps.units.values() if u.alive]
        if alive:
            u = alive[0]
            # Try a random hex on the board
            targets = [h for h in game.board if h != u.hex_id]
            if targets:
                target = random.choice(targets)
                mv = game.move_unit(cp, u.id, target)
                if mv.get("combat"):
                    print(f"  ⚔️ Combat: {mv.get('attacker_type')} vs {mv.get('defender_type')} → {mv.get('outcome')}")
                elif not mv.get("success"):
                    pass  # Movement failed, skip silently
                else:
                    print(f"  📍 {u.type} moved {mv['from_hex']} → {mv['to_hex']}")

        # Send a semantic message
        msg_text = random.choice(_MESSAGES)
        msg = game.send_semantic_message(cp, msg_text)
        if msg.get("success"):
            print(f"  💬 \"{msg_text}\" ({msg['word_count']} mots)")

        # End turn
        result = game.end_turn(cp)
        print(f"  Turn {turn}: {cp} → food_consumed={result.get('food_consumed', '?')}, next={result.get('next_player', '?')}")

        if result.get("famine_event"):
            fe = result["famine_event"]
            print(f"  ☠️ FAMINE! {fe.get('units_killed', '?')} unités tuées")

        if result.get("semantic_result"):
            sr = result["semantic_result"]
            print(f"  🧠 Sim={sr.get('cosine_similarity', '?')}, pts={sr.get('points_earned', '?')}, theme={sr.get('theme_similarity', '?')}")

        if result.get("game_over"):
            winner = result.get("winner", "unknown")
            print(f"\n🏆 GAME OVER -- Winner: {winner}")
            break

        turn += 1

    # Final state
    print("\n" + "=" * 50)
    print("FINAL STATE")
    print("=" * 50)
    state = game.get_full_state()
    print(json.dumps(state["players"], indent=2))
    print(f"Winner: {state.get('winner', 'none')}")


def cmd_reset(args: argparse.Namespace) -> None:
    """Reset a game instance."""
    if args.game_id in _game_instances:
        del _game_instances[args.game_id]
        print(f"Game '{args.game_id}' reset.")
    else:
        print(f"Game '{args.game_id}' was not running.")


def cmd_status(args: argparse.Namespace) -> None:
    """Show status of running games."""
    if not _game_instances:
        print("No games running.")
        return
    for gid, game in _game_instances.items():
        print(f"Game: {gid}")
        print(f"  Initialized: {game._initialized}")
        print(f"  Turn: {game.current_turn}")
        print(f"  Current player: {game.current_player_id}")
        print(f"  Winner: {game.winner or 'none'}")
        print(f"  Players: {', '.join(game.players.keys())}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Empires in the Fog CLI orchestrator"
    )
    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="Run a headless game with auto-play")
    p_run.add_argument("--game-id", default="default")
    p_run.add_argument("--players", nargs="+", default=["BotA", "BotB"])
    p_run.set_defaults(func=cmd_run)

    # reset
    p_reset = sub.add_parser("reset", help="Reset a game instance")
    p_reset.add_argument("--game-id", default="default")
    p_reset.set_defaults(func=cmd_reset)

    # status
    p_status = sub.add_parser("status", help="Show game status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
