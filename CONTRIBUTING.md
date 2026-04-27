# Contributing to Empires in the Fog

Thank you for your interest in contributing! This guide covers how to get started, the coding standards, and the process for getting your changes merged.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/empires-in-the-fog.git
   cd empires-in-the-fog
   ```
3. **Install dev dependencies**:
   ```bash
   pip install -e ".[dev,embeddings]"
   ```
4. **Run tests to verify setup**:
   ```bash
   pytest tests/ -v
   ```

## Development Workflow

### Branch Naming
Follow this convention:
- `feat/` — new features
- `fix/` — bug fixes
- `refactor/` — code restructuring (no behavior change)
- `docs/` — documentation changes
- `test/` — adding or fixing tests

Example: `feat/terrain-income-scaling`

### Making Changes

1. **Create a branch** from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **Write code** following the standards below
3. **Write/update tests** — all new features must have tests
4. **Run the test suite**:
   ```bash
   pytest tests/ -v
   ```
5. **Lint your code**:
   ```bash
   ruff check empires_in_the_fog/ tests/
   ```
6. **Commit with conventional messages**:
   ```bash
   git commit -m "feat: add terrain income scaling for forts"
   git commit -m "fix: correct food cost formula for zero-duration turns"
   git commit -m "test: add coverage for famine cascade scenarios"
   ```

### Pull Request Process

1. Push your branch to your fork
2. Open a PR against `main` with:
   - A clear title (conventional commit style: `type: description`)
   - Description of what changed and why
   - Link to any related issues
3. Wait for review — at least one maintainer must approve before merge

## Coding Standards

### Python Style
- **Target**: Python 3.12+
- **Line length**: 100 characters
- **Type hints**: Required for all public functions and methods
- **Docstrings**: Google-style for public functions, one-liners for private

### Imports Order
```python
# Standard library (alphabetical)
import json
import time
from dataclasses import dataclass

# Third-party (alphabetical)
from mcp.server.fastmcp import FastMCP

# Local application (alphabetical)
from empires_in_the_fog.config import GameConfig
from empires_in_the_fog.models import Unit
```

### Error Handling
- Return error dicts with `"error"` key from game methods — never raise exceptions for expected errors
- MCP tool errors should be descriptive: `{"error": "Not enough gold", "have": 3, "need": 6}`
- Use `raise` only for unexpected internal errors

### Game Engine Rules
- **DO NOT** modify game state directly from outside GameState methods
- **DO** return error dicts instead of raising for validation failures
- **DO** log events for all game state changes (use `_log_event`)
- All game rules are defined in `config.py` — avoid hardcoding constants

### Testing Standards
- **Naming**: `test_<functionality>_<scenario>`
  Example: `test_food_cost_formula_with_many_units`
- **Fixtures**: Use `@pytest.fixture` for common setup (e.g., `initialized_game`)
- **Assertions**: One logical concept per test, but multiple related asserts OK
- **Coverage**: New features should have unit + integration tests
- **No networking**: Tests must not require internet or external services

### Commit Messages
Use [Conventional Commits](https://www.conventionalcommits.org/) format:
```
type: description

Optional body explaining motivation and approach.

- Bullet point for significant sub-changes
- Another point if needed
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`

## Adding New Tools (MCP)

When adding a new MCP tool to the server:

1. Add the tool function in `server.py` with `@_mcp.tool()` decorator
2. Include a clear docstring describing the tool
3. Add inputSchema with all parameters
4. Add a test in `tests/test_game_state.py` or `tests/test_integration.py`
5. Update the README tools table
6. Update `CHANGELOG.md`

Example:
```python
@_mcp.tool()
def new_tool(player_id: str, param: str, game_id: str = "default") -> dict:
    """Description of what this tool does and when to use it."""
    return _get_game(game_id).new_tool(player_id, param)
```

## Architecture Guidelines

### Data Models
- Keep dataclasses in `models.py`
- Add `to_dict()` method for serialization
- Use `@classmethod def create(...)` factory for complex construction

### Configuration
- All tuning parameters go in `GameConfig`
- Do not hardcode `0.5`, `60`, `0.1`, etc. in game logic — use `self.config.X`
- Changing behavior for testing uses `GameConfig` overrides, not conditionals

### Embedding Service
- 3-tier fallback: sentence-transformers → TF-IDF → Jaccard
- Each tier must return a float between 0 and 1
- Never crash — always fall back gracefully

## Release Process

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md` — move [Unreleased] entries to new version section
3. Update spec document if game rules changed (`Empires_in_the_Fog_Spec_v1.X.md`)
4. Tag the release:
   ```bash
   git tag -a v1.4.1 -m "Release v1.4.1: fix food cost calculation"
   git push origin v1.4.1
   ```

## Questions?

Open an issue with the `question` label or ask in the discussion forum.
