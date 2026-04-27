# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Full MCP server with FastMCP (14 tools exposed)
- Game engine: turn system, hex grid, fog of war
- Combat resolution with terrain bonuses
- Food economy and attrition/famine mechanics
- Semantic diplomacy scoring via embeddings
- Spectator web UI with SSE real-time updates
- CLI orchestrator for headless game execution

### Changed
- v1.3 → v1.4: Replaced subjective LLM diplomacy with objective semantic scoring

## [1.4.0] - 2026-04-27

### Added
- Initial release based on specification v1.4
- MCP server (`server.py`) with 14 tools
- Game engine: GameState with full turn lifecycle
- Embedding service with 3-tier fallback (sentence-transformers → TF-IDF → Jaccard)
- Hexagonal map with fog of war (13 hexes, radius 2)
- Combat system with terrain modifiers
- Famine/attrition with power-based unit selection
- Semantic diplomacy with cosine similarity scoring
- CLI orchestrator (run, reset, status)
- Spectator web UI via Server-Sent Events
- Unit tests suite (40+ tests)
- GitHub-ready packaging (pyproject.toml, LICENSE, .gitignore, etc.)

[Unreleased]: https://github.com/your-org/empires-in-the-fog/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/your-org/empires-in-the-fog/releases/tag/v1.4.0
