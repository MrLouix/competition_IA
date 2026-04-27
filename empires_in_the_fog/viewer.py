#!/usr/bin/env python3
"""Empires in the Fog -- Spectator web UI with Server-Sent Events."""

from __future__ import annotations

import json
import queue
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from empires_in_the_fog.server import _game_instances, _get_game

_event_queues: dict[str, list[queue.Queue]] = {}
_next_event_id = [0]
_original_log_event = None


def broadcast_event(game_id: str, event: dict) -> None:
    """Broadcast an event to all SSE listeners for a game."""
    event["id"] = _next_event_id[0]
    _next_event_id[0] += 1

    if game_id not in _event_queues:
        _event_queues[game_id] = []

    data = json.dumps(event, default=str)
    for q in list(_event_queues.get(game_id, [])):
        try:
            q.put_nowait(data)
        except queue.Full:
            pass


def hook_into_log_event() -> None:
    """Monkey-patch GameState._log_event to broadcast via SSE."""
    from empires_in_the_fog.server import GameState
    global _original_log_event
    if _original_log_event is not None:
        return  # Already hooked

    _original_log_event = GameState._log_event

    def enhanced_log_event(self, event_type: str, data: dict) -> None:
        _original_log_event(self, event_type, data)
        broadcast_event(self.game_id, {
            "event_type": event_type,
            "game_id": self.game_id,
            "ts": time.time(),
            **data,
        })

    GameState._log_event = enhanced_log_event


class ViewerHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the spectator UI."""

    def __init__(self, *args, **kwargs):
        self._template_dir = Path(__file__).parent / "templates"
        super().__init__(*args, directory=str(Path(__file__).resolve().parent), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_html("index.html")
        elif path.startswith("/sse"):
            self._handle_sse(parsed.query)
        elif path.startswith("/api/state"):
            qs = parse_qs(parsed.query)
            game_id = qs.get("game_id", ["default"])[0]
            game = _get_game(game_id)
            self._json_response(game.get_full_state())
        elif path.startswith("/api/rules"):
            qs = parse_qs(parsed.query)
            game_id = qs.get("game_id", ["default"])[0]
            game = _get_game(game_id)
            self._json_response(game.get_game_rules())
        elif path.startswith("/favicon"):
            self.send_error(204)
        else:
            self.send_error(404)

    def _serve_html(self, filename: str) -> None:
        filepath = self._template_dir / filename
        if filepath.exists():
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(filepath.read_bytes())
        else:
            self.send_error(404)

    def _handle_sse(self, query_string: str) -> None:
        qs = parse_qs(query_string)
        game_id = qs.get("game_id", ["default"])[0]

        q: queue.Queue = queue.Queue(maxsize=1000)
        if game_id not in _event_queues:
            _event_queues[game_id] = []
        _event_queues[game_id].append(q)

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            while True:
                try:
                    event_data = q.get(timeout=30)
                    self.wfile.write(f"data: {event_data}\n\n".encode())
                    self.wfile.flush()
                except queue.Empty:
                    # Send keepalive comment
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if game_id in _event_queues and q in _event_queues[game_id]:
                _event_queues[game_id].remove(q)

    def _json_response(self, data: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def send_error(self, code: int, message: str = "") -> None:
        """Override to avoid stderr logging."""
        super().send_error(code, message)

    def log_message(self, fmt: str, *args) -> None:
        pass


def run_viewer(port: int = 8765) -> None:
    """Start the spectator UI server."""
    hook_into_log_event()
    server = HTTPServer(("0.0.0.0", port), ViewerHandler)
    print(f"Spectator UI -> http://0.0.0.0:{port}")
    print("Events will appear in real-time via SSE at /sse")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down viewer.")
        server.server_close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Empires in the Fog Spectator UI")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    args = parser.parse_args()
    run_viewer(args.port)


if __name__ == "__main__":
    main()
