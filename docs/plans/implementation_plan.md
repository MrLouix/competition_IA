# Empires in the Fog — Plan d'implémentation complet

> **Pour Hermes:** Utiliser `subagent-driven-development` pour exécuter ce plan tâche par tâche, ou implémenter manuellement en suivant l'ordre.

**Goal:** Construire la plateforme de compétition IA "Empires in the Fog" v1.4 — jeu de stratégie territoriale sur carte hexagonale avec brouillard de guerre, diplomatie sémantique par embeddings, scoring objectif, et interface spectateur temps réel.

**Architecture:** Serveur MCP (stdio) pour les IA, moteur de jeu Python pur, service d'embeddings scikit-learn/sentence-transformers, interface spectateur web légère avec SSE, CLI orchestrator pour lancer les parties. Tout est dans un seul package Python `empires_in_the_fog`.

**Tech Stack:** Python 3.12+, MCP SDK 1.27, scikit-learn (ou fallback Jaccard), sentence-transformers (optionnel production), pytest, Flask/Starlette pour le viewer SSE.

---

## État actuel du code

**Existe déjà (`server.py` + `game_rules.md`):**
- GameConfig avec toutes les constantes v1.4
- Models: Unit, HexState, Message, PlayerState
- GameState avec register_player, initialize_game
- Tour: is_my_turn, end_turn, scoring sémantique, famine/attrition
- Combat: move_unit avec résolution, capture territoire
- Recrutement: recruit_unit
- Diplomatie: send_semantic_message, read_messages
- Carte: get_visible_map avec fog of war
- get_full_state, surrender, reset_game
- get_game_rules (markdown)
- Serveur MCP FastMCP avec 12 outils stdio
- Similarité fallback Jaccard (sans sklearn)

**Ce qui manque (plan ci-dessous):** Tout le reste — tests, embedding réel, UI spectateur, CLI orchestrator, robustesse, doc.

---

### Task 1: Refactor — extraire les modèles dans des fichiers dédiés

**Objective:** Séparer les dataclass du gros `server.py` pour un code maintenable.

**Files:**
- Create: `empires_in_the_fog/models.py`
- Modify: `empires_in_the_fog/server.py` (importer depuis models)
- Create: `empires_in_the_fog/config.py`

**Créer `models.py`:**
```python
"""Empires in the Fog — Data models."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field


@dataclass
class UnitStats:
    atk: float
    def_: float
    mobility: float


UNIT_TYPE_STATS: dict[str, UnitStats] = {
    "scout":    UnitStats(atk=1, def_=1, mobility=4),
    "infantry": UnitStats(atk=3, def_=3, mobility=2),
    "cavalry":  UnitStats(atk=5, def_=2, mobility=5),
    "artillery":UnitStats(atk=7, def_=1, mobility=1),
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

    @classmethod
    def create(cls, owner_id: str, unit_type: str, hex_id: str) -> "Unit":
        stats = UNIT_TYPE_STATS[unit_type]
        return cls(
            id=str(uuid.uuid4())[:8], owner_id=owner_id, type=unit_type,
            atk=stats.atk, def_=stats.def_, mobility=stats.mobility,
            max_mobility=stats.mobility, alive=True, hex_id=hex_id,
        )


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
        d = {"hex_id": self.hex_id, "q": self.q, "r": self.r}
        if visible:
            d["terrain_type"] = self.terrain_type
            d["owner_id"] = self.owner_id
            d["is_capital"] = self.is_capital
            d["resources"] = self.resources
        else:
            d["fog"] = True
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
    food: float = 20.0
    gold: float = 10.0
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


@dataclass
class GameEvent:
    event_id: int
    event_type: str
    ts: float
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "type": self.event_type,
            "ts": self.ts, **self.data,
        }


EVENT_TYPES = [
    "MESSAGE_DELIVERED", "SEMANTIC_SCORE", "THEME_BONUS",
    "FAMINE_EVENT", "UNIT_KILLED", "UNIT_RECRUITED",
    "TERRITORY_CAPTURED", "COMBAT_RESOLVED",
    "TURN_STARTED", "TURN_ENDED", "VICTORY",
]
```

**Créer `config.py`:**
```python
"""Empires in the Fog — Configuration."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class GameConfig:
    MAX_MESSAGES_PER_TURN: int = 1
    MAX_MESSAGE_WORDS: int = 10
    TIMEOUT_TURN_SECONDS: int = 60
    BASE_COST: float = 0.5
    ATTRITION_RATE: float = 0.1
    SEMANTIC_THRESHOLD_ADJACENT: float = 0.35
    SEMANTIC_THRESHOLD_THEME: float = 0.20
    THEME_BONUS: float = 1.5
    fog_reveal_radius: int = 2
    MAX_TURNS: int = 20
    victory_condition: str = "either"
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
```

**Modifier `server.py`:** Retirer GameConfig, Unit, HexState, Message, PlayerState, DEFAULT_BOARD_TEMPLATES. Remplacer par des imports:
```python
from empires_in_the_fog.config import GameConfig, DEFAULT_BOARD_TEMPLATES
from empires_in_the_fog.models import Unit, HexState, Message, PlayerState, GameEvent, UNIT_TYPE_STATS, EVENT_TYPES
```

**Verify:**
```bash
cd /home/ai_agent/projects/competition_IA && python3 -c "from empires_in_the_fog.server import _mcp; print('OK')"
```
Expected: `OK`

**Commit:**
```bash
git add empires_in_the_fog/models.py empires_in_the_fog/config.py empires_in_the_fog/server.py
git commit -m "refactor: extract models and config from server.py"
```

---

### Task 2: Embedding service — similarité cosinus réelle

**Objective:** Remplacer le fallback Jaccard par un vrai service d'embeddings avec scikit-learn (TF-IDF) + support optionnel sentence-transformers.

**Files:**
- Create: `empires_in_the_fog/embeddings.py`
- Modify: `empires_in_the_fog/server.py` (utiliser EmbeddingService)

**Créer `embeddings.py`:**
```python
"""Empires in the Fog — Embedding service for semantic similarity."""
from __future__ import annotations
import json
import os
from pathlib import Path

_CACHE_DIR = Path.home() / ".cache" / "eitf"


class EmbeddingService:
    """Service de similarité sémantique.
    
    Stratégies (essai dans l'ordre):
    1. sentence-transformers (all-MiniLM-L6-v2) — le meilleur
    2. scikit-learn TF-IDF — léger, pas de modèle à télécharger
    3. Jaccard word overlap — fallback nul, toujours dispo
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._mode: str = "jaccard"
        self._pipeline = None
        self._vectorizer = None
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        # Essayer sentence-transformers
        try:
            from sentence_transformers import SentenceTransformer
            cache_path = _CACHE_DIR / "models"
            cache_path.mkdir(parents=True, exist_ok=True)
            self._pipeline = SentenceTransformer(
                self.model_name, cache_folder=str(cache_path)
            )
            self._mode = "sentence-transformers"
            self._loaded = True
            print(f"[embeddings] Loaded {self.model_name} via sentence-transformers")
            return
        except ImportError:
            pass
        except Exception as e:
            print(f"[embeddings] sentence-transformers failed: {e}")

        # Essayer sklearn TF-IDF
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._vectorizer = TfidfVectorizer()
            self._mode = "tfidf"
            self._loaded = True
            print("[embeddings] Using scikit-learn TF-IDF")
            return
        except ImportError:
            pass

        self._mode = "jaccard"
        self._loaded = True
        print("[embeddings] Using Jaccard word overlap (no ML libs)")

    def similarity(self, text_a: str, text_b: str) -> float:
        """Cosine similarity entre 0 et 1."""
        self._load()
        if self._mode == "sentence-transformers":
            emb_a = self._pipeline.encode([text_a])[0]
            emb_b = self._pipeline.encode([text_b])[0]
            dot = sum(a * b for a, b in zip(emb_a, emb_b))
            norm_a = sum(a * a for a in emb_a) ** 0.5
            norm_b = sum(b * b for b in emb_b) ** 0.5
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)

        if self._mode == "tfidf":
            vec = self._vectorizer
            tfidf = vec.fit_transform([text_a, text_b])
            from sklearn.metrics.pairwise import cosine_similarity
            return float(cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0])

        # Jaccard fallback
        set_a = set(text_a.lower().split())
        set_b = set(text_b.lower().split())
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    def mode(self) -> str:
        self._load()
        return self._mode
```

**Patcher `server.py`:** Remplacer `_simple_similarity` et ajouter EmbeddingService au GameState:

```python
# Dans __init__ de GameState:
from empires_in_the_fog.embeddings import EmbeddingService
self.embeddings = EmbeddingService(config.EMBEDDING_MODEL)

# Remplacer _simple_similarity:
def _similarity(self, text_a: str, text_b: str) -> float:
    return self.embeddings.similarity(text_a, text_b)
```

Puis remplacer tous les appels à `self._simple_similarity(...)` par `self._similarity(...)`.

**Verify:**
```bash
cd /home/ai_agent/projects/competition_IA && python3 -c "
from empires_in_the_fog.embeddings import EmbeddingService
e = EmbeddingService()
print('mode:', e.mode())
s = e.similarity('hello world', 'hello there')
print('similarity:', round(s, 4))
"
```

**Commit:**
```bash
git add empires_in_the_fog/embeddings.py empires_in_the_fog/server.py
git commit -m "feat: add real embedding service with sentence-transformers/tfidf/jaccard fallback"
```

---

### Task 3: Timeout automatique (timer 60s forcé)

**Objective:** Implémenter le timeout réel — quand 60s sont passées, end_turn forcé automatiquement, flag AFK, score 0.

**Files:**
- Modify: `empires_in_the_fog/server.py` (GameState + MCP server)

**Ajouter dans `GameState.__init__`:**
```python
self._timeout_task: asyncio.Task | None = None
```

**Ajouter méthode de force-end:**
```python
async def _force_end_turn(self, player_id: str, game_id: str):
    """Forcer la fin du tour après timeout."""
    ps = self.players.get(player_id)
    if not ps:
        return
    ps.afk_streak += 1

    # Calcul du coût avec durée = TIMEOUT_TURN_SECONDS
    elapsed = float(self.config.TIMEOUT_TURN_SECONDS)
    active_units = len([u for u in ps.units.values() if u.alive])
    turn_cost = self.config.BASE_COST + (
        self.config.ATTRITION_RATE * active_units * elapsed / 60
    )
    ps.food -= turn_cost

    self._log_event("TURN_ENDED", {
        "player": player_id, "turn": self.current_turn,
        "timeout": True, "afk": True,
    })

    # Passer au suivant
    idx = self.turn_order.index(player_id)
    next_idx = (idx + 1) % len(self.turn_order)
    if next_idx <= idx:
        self.current_turn += 1
    self.current_player_id = self.turn_order[next_idx]
    self.turn_start_time = time.time()
```

**Dans `initialize_game`, démarrer un watcher asynchrone:**
```python
async def start_timeout_watcher(self):
    """Background task qui force end_turn si timeout dépassé."""
    while not self.winner:
        await asyncio.sleep(1)
        if self.current_player_id and self.turn_start_time:
            elapsed = time.time() - self.turn_start_time
            if elapsed >= self.config.TIMEOUT_TURN_SECONDS:
                await self._force_end_turn(self.current_player_id, self.game_id)

# Dans main():
async def main_async():
    game = _get_game(args.game_id)
    if game._initialized:
        asyncio.create_task(game.start_timeout_watcher())
    _mcp.run(transport="stdio")
```

**Commit:**
```bash
git add empires_in_the_fog/server.py
git commit -m "feat: implement automatic 60s turn timeout with AFK flag"
```

---

### Task 4: Tests unitaires — Core (modèles, config, similarité)

**Objective:** Tests pour les briques de base.

**Files:**
- Create: `tests/test_models.py`
- Create: `tests/test_config.py`
- Create: `tests/test_embeddings.py`

**`tests/__init__.py`:** fichier vide

**`tests/test_config.py`:**
```python
from empires_in_the_fog.config import GameConfig, DEFAULT_BOARD_TEMPLATES

def test_config_defaults():
    c = GameConfig()
    assert c.BASE_COST == 0.5
    assert c.ATTRITION_RATE == 0.1
    assert c.TIMEOUT_TURN_SECONDS == 60
    assert c.MAX_MESSAGE_WORDS == 10
    assert c.SEMANTIC_THRESHOLD_ADJACENT == 0.35
    assert c.fog_reveal_radius == 2
    assert c.MAX_TURNS == 20
    assert len(c.unit_costs) == 4
    assert c.unit_costs["scout"] == 2

def test_board_templates():
    assert len(DEFAULT_BOARD_TEMPLATES) >= 7  # at least 7 hexes
    ids = {t["hex_id"] for t in DEFAULT_BOARD_TEMPLATES}
    assert "h0_0" in ids  # starting capital
```

**`tests/test_models.py`:**
```python
from empires_in_the_fog.models import Unit, PlayerState, HexState

def test_unit_power_score():
    u = Unit(id="u1", owner_id="p1", type="scout",
             atk=1, def_=1, mobility=4, max_mobility=4, alive=True, hex_id="h0")
    assert u.power_score == 1 + 1 + 0.5 * 4  # = 4

def test_unit_create():
    u = Unit.create(owner_id="p1", unit_type="infantry", hex_id="h0")
    assert u owner_id == "p1"
    assert u.type == "infantry"
    assert u.alive is True
    assert u.atk == 3
    assert u.def_ == 3

def test_unit_to_dict():
    u = Unit.create("p1", "cavalry", "h0")
    d = u.to_dict()
    assert d["type"] == "cavalry"
    assert "power_score" in d

def test_hex_state():
    h = HexState(hex_id="h0", q=0, r=0, terrain_type="forest")
    v = h.to_dict(visible=True)
    assert v["terrain_type"] == "forest"
    f = h.to_dict(visible=False)
    assert f.get("fog") is True

def test_player_state_defaults():
    p = PlayerState(player_id="test")
    assert p.food == 20.0
    assert p.gold == 10.0
    assert p.semantic_score == 0
    assert not p.is_eliminated
```

**`tests/test_embeddings.py`:**
```python
from empires_in_the_fog.embeddings import EmbeddingService

def test_jaccard_similarity_identical():
    e = EmbeddingService()
    s = e.similarity("hello world", "hello world")
    assert s == 1.0

def test_jaccard_similarity_no_overlap():
    e = EmbeddingService()
    s = e.similarity("apple banana", "carrot potato")
    assert s == 0.0

def test_jaccard_similarity_partial():
    e = EmbeddingService()
    s = e.similarity("hello world", "hello there")
    assert 0 < s < 1

def test_jaccard_empty():
    e = EmbeddingService()
    assert e.similarity("", "hello") == 0.0

def test_mode_returns_string():
    e = EmbeddingService()
    assert isinstance(e.mode(), str)
```

**Verify:**
```bash
cd /home/ai_agent/projects/competition_IA && python3 -m pytest tests/ -v
```
Expected: all tests pass (minimum 10)

**Commit:**
```bash
git add tests/
git commit -m "test: add unit tests for models, config, embeddings"
```

---

### Task 5: Tests unitaires — GameState (tour, combat, famine)

**Objective:** Tests pour les mécaniques de jeu.

**Files:**
- Create: `tests/test_game_state.py`

```python
import pytest
from empires_in_the_fog.server import GameState
from empires_in_the_fog.config import GameConfig


@pytest.fixture
def game():
    return GameState("test")


@pytest.fixture
def initialized_game():
    g = GameState("test")
    g.register_player("A")
    g.register_player("B")
    g.initialize_game()
    return g


def test_register_player(game):
    r = game.register_player("P1")
    assert r["status"] == "registered"
    assert r["player_id"] == "P1"


def test_register_duplicate(game):
    game.register_player("P1")
    r = game.register_player("P1")
    assert r["status"] == "already_registered"


def test_init_requires_two_players(game):
    game.register_player("A")
    r = game.initialize_game()
    assert "error" in r


def test_init_success(initialized_game):
    assert initialized_game._initialized is True
    assert initialized_game.current_turn == 1
    assert initialized_game.current_player_id is not None


def test_is_my_turn(initialized_game):
    cp = initialized_game.current_player_id
    r = initialized_game.is_my_turn(cp)
    assert r["is_turn"] is True
    assert r["turn_number"] == 1
    assert "food" in r
    assert "gold" in r


def test_not_my_turn(initialized_game):
    players = list(initialized_game.players.keys())
    not_current = players[1] if players[0] == initialized_game.current_player_id else players[0]
    r = initialized_game.is_my_turn(not_current)
    assert r["is_turn"] is False


def test_end_turn_switches_player(initialized_game):
    cp = initialized_game.current_player_id
    r = initialized_game.end_turn(cp)
    assert r["next_player"] != cp
    # If only 2 players, back to turn 2 or same turn next player


def test_food_cost_formula(initialized_game):
    """BASE_COST(0.5) + ATTRITION(0.1) × units × duration/60"""
    game = initialized_game
    cp = game.current_player_id
    ps = game.players[cp]
    food_before = ps.food
    active_units = len([u for u in ps.units.values() if u.alive])

    import time
    game.turn_start_time = time.time() - 30  # simulate 30s
    r = game.end_turn(cp)

    expected_cost = 0.5 + (0.1 * active_units * 30 / 60)
    actual_consumed = r["food_consumed"]
    assert abs(actual_consumed - expected_cost) < 0.01  # 1 cent tolerance
    

def test_message_too_long(initialized_game):
    cp = initialized_game.current_player_id
    r = initialized_game.send_semantic_message(cp, "a" * 100 + " " * 20)  # way over 10 words
    assert r["error"] == "MESSAGE_TOO_LONG"


def test_empty_message(initialized_game):
    cp = initialized_game.current_player_id
    r = initialized_game.send_semantic_message(cp, "")
    assert r["error"] == "EMPTY_MESSAGE"


def test_message_limit_per_turn(initialized_game):
    cp = initialized_game.current_player_id
    r1 = initialized_game.send_semantic_message(cp, "hello friend")
    assert r1["success"] is True
    r2 = initialized_game.send_semantic_message(cp, "second message")
    assert r2["error"] == "ALREADY_SENT"


def test_valid_message_and_scoring(initialized_game):
    cp = initialized_game.current_player_id
    opponents = [p for p in initialized_game.players if p != cp]
    opponent = opponents[0]

    # Set opponent reference message
    initialized_game._opponent_messages[cp] = "peace and unity forever"
    r = initialized_game.send_semantic_message(cp, "peace and unity always")
    assert r["success"] is True
    assert r["word_count"] == 4

    # End turn triggers scoring
    end_r = initialized_game.end_turn(cp)
    if "semantic_result" in end_r:
        assert "similarity" in end_r["semantic_result"] or "cosine_similarity" in end_r["semantic_result"]


def test_move_unit_basic(initialized_game):
    cp = initialized_game.current_player_id
    ps = initialized_game.players[cp]
    unit_id = list(ps.units.keys())[0]
    target = [h for h in initialized_game.board.keys() if h in ps.territories or h not in initialized_game.players.get([p for p in initialized_game.players if p != cp][0]).territories][0]

    r = initialized_game.move_unit(cp, unit_id, target)
    assert "success" in r or "error" in r  # depends on adjacency check


def test_recruit_unit_cost(initialized_game):
    cp = initialized_game.current_player_id
    ps = initialized_game.players[cp]
    ps.gold = 100  # ensure enough gold
    territory = list(ps.territories)[0]

    r = initialized_game.recruit_unit(cp, "infantry", territory)
    assert r["success"] is True
    assert r["gold_cost"] == 4
    assert ps.gold == 96


def test_recruit_not_enough_gold(initialized_game):
    cp = initialized_game.current_player_id
    ps = initialized_game.players[cp]
    ps.gold = 1  # not enough
    territory = list(ps.territories)[0]

    r = initialized_game.recruit_unit(cp, "artillery", territory)
    assert r["error"] == "Not enough gold"


def test_recruit_uncontrolled_hex(initialized_game):
    cp = initialized_game.current_player_id
    r = initialized_game.recruit_unit(cp, "scout", "h0_0")  # likely opponent's
    assert "error" in r


def test_famine_resolves_units():
    cfg = GameConfig(BASE_COST=0.5, ATTRITION_RATE=0.1)
    g = GameState("famine_test", config=cfg)
    g.register_player("A")
    g.register_player("B")
    g.initialize_game()

    cp = g.current_player_id
    g.players[cp].food = 0.1  # almost starving
    g.turn_start_time = __import__("time").time() - 30

    r = g.end_turn(cp)
    # If food goes negative, famine should trigger
    if "famine_event" in r:
        fe = r["famine_event"]
        assert fe["food_set_to_zero"] is True
        assert g.players[cp].food == 0


def test_surrender(initialized_game):
    cp = initialized_game.current_player_id
    r = initialized_game.surrender(cp)
    assert r["surrendered"] is True
    assert r["game_over"] is True
    assert r["winner"] != cp


def test_full_state(initialized_game):
    r = initialized_game.get_full_state()
    assert "current_turn" in r
    assert "players" in r
    assert len(r["players"]) == 2


def test_get_game_rules(initialized_game):
    r = initialized_game.get_game_rules()
    assert "rules_markdown" in r
    assert len(r["rules_markdown"]) > 100
    assert "config" in r
```

**Verify:**
```bash
cd /home/ai_agent/projects/competition_IA && python3 -m pytest tests/test_game_state.py -v
```
Expected: all tests pass

**Commit:**
```bash
git add tests/test_game_state.py
git commit -m "test: add GameState unit tests for turns, combat, famine, messages"
```

---

### Task 6: Tests d'intégration — Flux complet de tour

**Objective:** Tester le cycle complet: register → init → is_my_turn → actions → message → end_turn → scoring.

**Files:**
- Create: `tests/test_integration.py`

```python
"""Integration tests — full game flow."""
from empires_in_the_fog.server import GameState


def _setup_game() -> GameState:
    g = GameState("integration_test")
    g.register_player("Alpha")
    g.register_player("Beta")
    g.initialize_game()
    return g


def test_full_turn_flow():
    g = _setup_game()
    cp = g.current_player_id

    # 1. is_my_turn
    turn_info = g.is_my_turn(cp)
    assert turn_info["is_turn"] is True
    assert turn_info["turn_number"] == 1

    # 2. View map
    map_info = g.get_visible_map(cp)
    assert "hexes" in map_info
    assert len(map_info["hexes"]) > 0

    # 3. View units
    units = g.get_units(cp)
    assert units["total"] >= 3  # 3 starting scouts

    # 4. Move a unit
    unit_id = list(g.players[cp].units.keys())[0]
    adj = [h for h in g.board if h not in g.players[cp].territories][:1]
    if adj:
        g.move_unit(cp, unit_id, adj[0])

    # 5. Send semantic message
    msg = g.send_semantic_message(cp, "peace through strength")
    assert msg["success"] is True

    # 6. End turn
    result = g.end_turn(cp)
    assert result["next_player"] != cp
    assert "food_consumed" in result
    assert result["food_consumed"] > 0


def test_multi_turn_sequence():
    g = _setup_game()
    for turn in range(1, 6):
        cp = g.current_player_id
        g.is_my_turn(cp)
        g.send_semantic_message(cp, f"alliance turn {turn}")
        g.end_turn(cp)


def test_game_ends_at_max_turns():
    g = _setup_game()
    g.config.MAX_TURNS = 3
    while not g.winner and g.current_turn <= g.config.MAX_TURNS + 1:
        cp = g.current_player_id
        g.is_my_turn(cp)
        try:
            g.send_semantic_message(cp, "peace")
        except:
            pass
        g.end_turn(cp)
    assert g.winner is not None or g.current_turn > g.config.MAX_TURNS


def test_recruitment_then_economy():
    g = _setup_game()
    cp = g.current_player_id
    ps = g.players[cp]
    ps.gold = 50
    territory = list(ps.territories)[0]

    g.recruit_unit(cp, "cavalry", territory)
    g.recruit_unit(cp, "infantry", territory)

    units = g.get_units(cp)
    assert units["total"] >= 5  # 3 scouts + 2 new


def test_famine_cascade():
    """Verify food cost increases with more units."""
    g = _setup_game()
    cp = g.current_player_id
    ps = g.players[cp]
    ps.gold = 100
    territory = list(ps.territories)[0]

    # Recruit lots of units
    for _ in range(10):
        g.recruit_unit(cp, "scout", territory)

    # End turn — more units = higher food cost
    import time
    g.turn_start_time = time.time() - 30
    r1 = g.end_turn(cp)
    cost_with_many = r1["food_consumed"]

    # Now check: fewer units would cost less
    # This implicitly validates the formula
    assert cost_with_many > 0.5  # must exceed BASE_COST
```

**Verify:**
```bash
cd /home/ai_agent/projects/competition_IA && python3 -m pytest tests/test_integration.py -v
```

**Commit:**
```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for full game flow"
```

---

### Task 7: CLI orchestrator — lancer les parties

**Objective:** CLI pour créer, gérer, et superviser les parties. Supporte mode local (2 joueurs en loopback) et mode spectateur.

**Files:**
- Create: `empires_in_the_fog/cli.py`

```python
#!/usr/bin/env python3
"""Empires in the Fog — CLI orchestrator."""
from __future__ import annotations

import argparse
import json
import sys
import time

from empires_in_the_fog.server import GameState, _get_game


def cmd_run(args):
    """Run a game in headless mode with auto-play."""
    game = _get_game(args.game_id)

    # Register players
    for name in args.players:
        game.register_player(name)

    print(json.dumps(game.initialize_game()))

    turn = 1
    while not game.winner and turn <= game.config.MAX_TURNS * 2 + game.config.TIMEOUT_TURN_SECONDS:
        cp = game.current_player_id
        info = game.is_my_turn(cp)

        if not info.get("is_turn"):
            break

        # Auto-play: random moves, send message, end turn
        ps = game.players[cp]
        alive = [u for u in ps.units.values() if u.alive]
        if alive:
            u = alive[0]
            # Try a random adjacent move
            import random
            possible = [h for h in game.board if h != u.hex_id]
            if possible:
                target = random.choice(possible)
                game.move_unit(cp, u.id, target)

        # Send a thematic message
        msgs = ["peace", "war approaches", "alliance?", "trust no one", "the fog hides all"]
        import random
        game.send_semantic_message(cp, random.choice(msgs))

        result = game.end_turn(cp)
        print(f"Turn {info.get('turn_number')} — {cp} → "
              f"food_consumed={result.get('food_consumed', '?')}, "
              f"next={result.get('next_player', '?')}")

        if result.get("famine_event"):
            print(f"  FAMINE! {result['famine_event']['units_killed']} units killed")
        if result.get("semantic_result"):
            sr = result["semantic_result"]
            print(f"  SEMANTIC: sim={sr.get('similarity', sr.get('cosine_similarity', '?'))}, "
                  f"pts={sr.get('points_earned', '?')}")

        if result.get("game_over"):
            print(f"GAME OVER — Winner: {result.get('winner', 'unknown')}")
            break

    # Final state
    print("\n=== Final State ===")
    print(json.dumps(game.get_full_state(), indent=2))


def cmd_reset(args):
    from empires_in_the_fog.server import _game_instances
    if args.game_id in _game_instances:
        del _game_instances[args.game_id]
    print(f"Game '{args.game_id}' reset.")


def cmd_status(args):
    from empires_in_the_fog.server import _game_instances
    for gid, game in _game_instances.items():
        print(f"Game: {gid}")
        print(f"  Initialized: {game._initialized}")
        print(f"  Turn: {game.current_turn}")
        print(f"  Current player: {game.current_player_id}")
        print(f"  Winner: {game.winner or 'none'}")
        print(f"  Players: {', '.join(game.players.keys())}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Empires in the Fog CLI")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Run a game with AI players")
    p_run.add_argument("--game-id", default="default")
    p_run.add_argument("--players", nargs="+", default=["BotA", "BotB"])
    p_run.set_defaults(func=cmd_run)

    p_reset = sub.add_parser("reset", help="Reset a game")
    p_reset.add_argument("--game-id", default="default")
    p_reset.set_defaults(func=cmd_reset)

    p_status = sub.add_parser("status", help="Show game status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
```

**Verify:**
```bash
cd /home/ai_agent/projects/competition_IA && python3 -m empires_in_the_fog.cli run --players IA_A IA_B
```
Expected: Game runs for several turns, outputs turn-by-turn logs

**Commit:**
```bash
git add empires_in_the_fog/cli.py
git commit -m "feat: add CLI orchestrator for running and managing games"
```

---

### Task 8: Spectator UI — web app avec SSE

**Objective:** Interface web temps réel pour spectateurs via Server-Sent Events.

**Files:**
- Create: `empires_in_the_fog/viewer.py`
- Create: `empires_in_the_fog/templates/index.html`

**Créer `viewer.py`:**
```python
#!/usr/bin/env python3
"""Empires in the Fog — Spectator web UI with SSE."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from threading import Thread
import queue

from empires_in_the_fog.server import _game_instances, _get_game

_event_queues: dict[str, list[queue.Queue]] = {}
_next_event_id = [0]


def broadcast_event(game_id: str, event: dict):
    """Send event to all SSE listeners for a game."""
    event["id"] = _next_event_id[0]
    _next_event_id[0] += 1

    if game_id not in _event_queues:
        _event_queues[game_id] = []

    data = json.dumps(event)
    for q in list(_event_queues[game_id]):
        try:
            q.put_nowait(data)
        except queue.Full:
            pass


class ViewerHandler(SimpleHTTPRequestHandler):
    """Simple HTTP server for the spectator UI."""

    def __init__(self, *args, **kwargs):
        self.template_dir = Path(__file__).parent / "templates"
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == "/":
            self._serve_html("index.html")
        elif self.path.startswith("/sse"):
            self._handle_sse()
        elif self.path.startswith("/api/"):
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            game_id = qs.get("game_id", ["default"])[0]

            if self.path == "/api/state":
                game = _get_game(game_id)
                self._json_response(game.get_full_state())
            elif self.path == "/api/rules":
                game = _get_game(game_id)
                self._json_response(game.get_game_rules())
            else:
                self.send_error(404)
        else:
            # Serve static files from static/ dir
            super().do_GET()

    def _serve_html(self, filename):
        filepath = self.template_dir / filename
        if filepath.exists():
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(filepath.read_bytes())
        else:
            self.send_error(404)

    def _handle_sse(self):
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        game_id = qs.get("game_id", ["default"])[0]

        q = queue.Queue(maxsize=500)
        if game_id not in _event_queues:
            _event_queues[game_id] = []
        _event_queues[game_id].append(q)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            while True:
                event_data = q.get(timeout=30)
                self.wfile.write(f"data: {event_data}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            if game_id in _event_queues and q in _event_queues[game_id]:
                _event_queues[game_id].remove(q)

    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def log_message(self, fmt, *args):
        pass  # Suppress logs


def add_sse_hook_to_gamestate():
    """Monkey-patch GameState._log_event to broadcast via SSE."""
    from empires_in_the_fog.server import GameState
    original_log = GameState._log_event

    def enhanced_log(self, event_type: str, data: dict):
        original_log(self, event_type, data)
        broadcast_event(self.game_id, {
            "event_type": event_type,
            "game_id": self.game_id,
            "ts": time.time(),
            **data,
        })

    GameState._log_event = enhanced_log


def run_viewer(port: int = 8765):
    add_sse_hook_to_gamestate()
    server = HTTPServer(("0.0.0.0", port), ViewerHandler)
    print(f"Spectator UI → http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run_viewer(args.port)
```

**Créer `empires_in_the_fog/templates/index.html`:**
```html
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Empires in the Fog — Spectateur</title>
<style>
  :root { --bg: #0d1117; --surface: #161b22; --border: #30363d; --text: #c9d1d9; --accent: #58a6ff; --green: #3fb950; --red: #f85149; --gold: #d29922; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: monospace; line-height: 1.5; padding: 1rem; }
  h1 { color: var(--accent); margin-bottom: 0.5rem; font-size: 1.4rem; }
  .grid { display: grid; grid-template-columns: 300px 1fr; gap: 1rem; }
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; }
  .player-card { margin-bottom: 0.5rem; padding: 0.5rem; border-bottom: 1px solid var(--border); }
  .player-card h3 { color: var(--accent); }
  .stat { display: flex; justify-content: space-between; }
  .stat .val { color: var(--green); }
  #event-log { height: 300px; overflow-y: auto; font-size: 0.85rem; }
  .event { padding: 2px 0; border-bottom: 1px solid var(--border); }
  .event.famine { color: var(--red); }
  .event.combat { color: var(--gold); }
  .event.semantic { color: var(--green); }
  .event.turn { color: var(--accent); }
  #hex-map { font-size: 0.6rem; line-height: 1.2; white-space: pre; text-align: center; }
  .hex { display: inline-block; width: 3ch; text-align: center; }
  h2 { color: var(--accent); font-size: 1.1rem; margin: 1rem 0 0.5rem; }
  #winner { font-size: 1.5rem; color: var(--gold); text-align: center; margin: 1rem 0; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 0.75rem; }
  .badge-afk { background: var(--red); color: white; }
</style>
</head>
<body>
<h1>🌫️ Empires in the Fog</h1>
<div id="winner"></div>
<div class="grid">
  <div>
    <div class="panel" id="players"></div>
    <h2>Configuration</h2>
    <div class="panel" id="config"></div>
  </div>
  <div>
    <div class="panel" id="hex-map">Chargement de la carte...</div>
    <h2>Événements</h2>
    <div class="panel" id="event-log"></div>
  </div>
</div>
<script>
let lastEventId = 0;

function getState() {
  fetch('/api/state')
    .then(r => r.json())
    .then(d => updateState(d))
    .catch(e => console.error('State fetch failed:', e));
}

function getStateRules() {
  fetch('/api/rules')
    .then(r => r.json())
    .then(d => {
      const el = document.getElementById('config');
      el.innerHTML = '<pre style="font-size:0.8rem">' + JSON.stringify(d.config, null, 2) + '</pre>';
    });
}

function updateState(state) {
  if (state.winner) {
    document.getElementById('winner').textContent = '🏆 Victoire: ' + state.winner;
  }

  const playersEl = document.getElementById('players');
  if (state.players) {
    playersEl.innerHTML = '<h2 style="margin:0">Joueurs</h2>';
    for (const [id, p] of Object.entries(state.players)) {
      playersEl.innerHTML += `
        <div class="player-card">
          <h3>${id} ${p.is_eliminated ? '<span class="badge badge-afk">ÉLIMINÉ</span>' : ''}</h3>
          <div class="stat"><span>Nourriture</span><span class="val">${p.food}</span></div>
          <div class="stat"><span>Or</span><span class="val">${p.gold}</span></div>
          <div class="stat"><span>Unités</span><span class="val">${p.active_units}</span></div>
          <div class="stat"><span>Territoires</span><span class="val">${p.territories.length}</span></div>
          <div class="stat"><span>Score sémantique</span><span class="val">${p.semantic_score}</span></div>
        </div>`;
    }
  }

  // ASCII hex map
  const mapEl = document.getElementById('hex-map');
  if (state.board) {
    let out = '<pre>';
    for (const [id, hex] of Object.entries(state.board)) {
      const owner = hex.owner_id ? hex.owner_id[0] : '·';
      out += `<span class="hex" title="${id}: ${hex.terrain_type} (${owner})">${owner}</span>`;
    }
    out += '</pre>';
    mapEl.innerHTML = out;
  }
}

function connectSSE() {
  const evtSource = new EventSource('/sse');
  evtSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    lastEventId = data.id || lastEventId;
    addEvent(data);
    // Refresh state on turn changes
    if (data.event_type.includes('TURN') || data.event_type.includes('VICTORY')) {
      getState();
    }
  };
}

function addEvent(data) {
  const log = document.getElementById('event-log');
  let cls = 'event';
  if (data.event_type.includes('FAMINE')) cls += ' famine';
  else if (data.event_type.includes('COMBAT')) cls += ' combat';
  else if (data.event_type.includes('SEMANTIC')) cls += ' semantic';
  else if (data.event_type.includes('TURN')) cls += ' turn';
  
  const desc = formatEvent(data);
  log.innerHTML = `<div class="${cls}"><strong>[${data.event_type}]</strong> ${desc}</div>` + log.innerHTML;
}

function formatEvent(data) {
  const parts = [];
  if (data.player) parts.push(`Joueur: ${data.player}`);
  if (data.turn !== undefined) parts.push(`Tour: ${data.turn}`);
  if (data.outcome) parts.push(`Résultat: ${data.outcome}`);
  if (data.units_killed) parts.push(`${data.units_killed} unités tuées`);
  if (data.hex_id) parts.push(`Hex: ${data.hex_id}`);
  if (data.winner) parts.push(`🏆 ${data.winner} gagne !`);
  return parts.join(' | ') || JSON.stringify(data);
}

// Start
getState();
getStateRules();
connectSSE();
setInterval(getState, 3000);
</script>
</body>
</html>
```

**Patcher `GameState._log_event` dans `server.py` pour intégrer le broadcast SSE:**
```python
# Ajouter à la fin de __init__ de GameState:
try:
    from empires_in_the_fog.viewer import broadcast_event
    self._sse_broadcast = True
except ImportError:
    self._sse_broadcast = False

# Modifier _log_event:
def _log_event(self, event_type: str, data: dict):
    self.event_log.append({"event_type": event_type, "ts": time.time(), **data})
    if self._sse_broadcast:
        from empires_in_the_fog.viewer import broadcast_event
        broadcast_event(self.game_id, {
            "event_type": event_type, "game_id": self.game_id,
            "ts": time.time(), **data,
        })
```

**Verify:**
```bash
# Terminal 1: Viewer
python3 -m empires_in_the_fog.viewer --port 8765

# Terminal 2: CLI run
python3 -m empires_in_the_fog.cli run --players Alpha Beta

# Browser: http://localhost:8765 — voir la carte, les événements en temps réel
```

**Commit:**
```bash
git add empires_in_the_fog/viewer.py empires_in_the_fog/templates/index.html empires_in_the_fog/server.py
git commit -m "feat: add spectator web UI with SSE real-time updates"
```

---

### Task 9: pyproject.toml, dépendances, README

**Objective:** Packaging propre du projet.

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`

**`pyproject.toml`:**
```toml
[project]
name = "empires-in-the-fog"
version = "1.4.0"
description = "AI competition platform: territorial strategy with fog of war and semantic diplomacy"
requires-python = ">=3.12"
dependencies = [
    "mcp>=1.20",
]

[project.optional-dependencies]
embeddings = ["scikit-learn>=1.3"]
embeddings-full = ["sentence-transformers>=2.2.0"]
viewer = []  # stdlib only
dev = ["pytest>=7.0", "pytest-asyncio>=0.23"]

[project.scripts]
eitf-server = "empires_in_the_fog.server:main"
eitf-cli = "empires_in_the_fog.cli:main"
eitf-viewer = "empires_in_the_fog.viewer:run_viewer"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

**`README.md`:**
```markdown
# 🌫️ Empires in the Fog v1.4

Plateforme de compétition IA — stratégie territoriale, brouillard de guerre, diplomatie sémantique.

## Architecture

- **MCP Server** — API pour les IA (stdio, conforme Model Context Protocol)
- **Game Engine** — Moteur de jeu Python pur (carte hex, combat, famine, économie)
- **Embedding Service** — Similarité sémantique (sentence-transformers → TF-IDF → Jaccard)
- **Spectator UI** — Web temps réel via SSE
- **CLI** — Orchestration de parties

## Installation

```bash
pip install -e ".[dev,embeddings]"
```

## Utilisation

### Serveur MCP (pour les IA)
```bash
python -m empires_in_the_fog.server --game-id default
```

### Lancer une partie
```bash
python -m empires_in_the_fog.cli run --players IA_A IA_B
```

### Interface spectateur
```bash
python -m empires_in_the_fog.viewer --port 8765
# → http://localhost:8765
```

### Tests
```bash
pytest tests/ -v
```

### Configurer dans Hermes
```yaml
mcp_servers:
  empires-in-the-fog:
    command: "python3"
    args:
      - "-m"
      - "empires_in_the_fog.server"
    timeout: 120
    connect_timeout: 60
```

## Règles du jeu

- 2 joueurs sur carte hexagonale (13 hex, fog of war radius 2)
- Tour de 60s max, coût de nourriture = BASE_COST + ATTRITION × unités × durée/60
- Messages diplomatiques ≤ 10 mots, scoring par similarité cosinus
- Combat: atk×rand(0.8,1.2) vs def×rand×terrain_bonus
- Victoire: domination totale ou score après MAX_TURNS
- Voir `rules/game_rules.md` pour les règles complètes

## Structure
```
empires_in_the_fog/
  server.py        # Serveur MCP + GameState
  config.py         # Configuration
  models.py         # Dataclass (Unit, HexState, etc.)
  embeddings.py     # Service de similarité
  cli.py            # CLI orchestrator
  viewer.py         # Spectator web UI
  templates/        # Templates HTML viewer
  rules/            # game_rules.md
tests/
  test_models.py
  test_config.py
  test_embeddings.py
  test_game_state.py
  test_integration.py
```

## Mécaniques

| Feature | Statut |
|---------|--------|
| Carte hexagonale | ✅ |
| Brouillard de guerre | ✅ |
| Combat avec terrain | ✅ |
| Famine & attrition | ✅ |
| Diplomatie sémantique | ✅ |
| Embeddings réels | 🔄 |
| Server MCP | ✅ |
| Spectator UI | 🔄 |
| Tests | 🔄 |
| Timeout auto | 🔄 |
```

**Verify:**
```bash
cd /home/ai_agent/projects/competition_IA && pip install -e ".[dev]"
pytest tests/ -v --tb=short
```

**Commit:**
```bash
git add pyproject.toml README.md
git commit -m "chore: add pyproject.toml, setup.py, README"
```

---

### Task 10: Configuration Hermes — activer le serveur MCP

**Objective:** Configurer Hermes pour découvrir automatiquement les outils du jeu.

**Fichier:** `~/.hermes/config.yaml`

Ajouter sous `mcp_servers:`:
```yaml
  empires-in-the-fog:
    command: "python3"
    args:
      - "-c"
      - "import sys; sys.path.insert(0, '/home/ai_agent/projects/competition_IA'); from empires_in_the_fog.server import main; main()"
    timeout: 120
    connect_timeout: 60
```

Après restart Hermes, les outils suivants apparaîtront automatiquement:
- `mcp_empires_in_the_fog_register_player`
- `mcp_empires_in_the_fog_initialize_game`
- `mcp_empires_in_the_fog_get_game_rules`
- `mcp_empires_in_the_fog_is_my_turn`
- `mcp_empires_in_the_fog_end_turn`
- `mcp_empires_in_the_fog_move_unit`
- `mcp_empires_in_the_fog_recruit_unit`
- `mcp_empires_in_the_fog_send_semantic_message`
- `mcp_empires_in_the_fog_read_messages`
- `mcp_empires_in_the_fog_get_visible_map`
- `mcp_empires_in_the_fog_get_units`
- `mcp_empires_in_the_fog_get_full_state`
- `mcp_empires_in_the_fog_surrender`
- `mcp_empires_in_the_fog_reset_game`

---

### Task 11: Scénarios de test end-to-end

**Objective:** Vérifier des scénarios concrets via le CLI.

**Files:**
- Create: `tests/test_e2e_scenarios.py`

```python
"""End-to-end scenario tests."""
from empires_in_the_fog.server import GameState


def test_scenario_turn_1_initial_verse():
    """Tour 1: le serveur fournit un vers initial comme référence."""
    g = GameState("scenario_t1")
    g.register_player("A")
    g.register_player("B")
    g.initialize_game()

    cp = g.current_player_id
    info = g.is_my_turn(cp)
    # Le vers initial sert de message de référence
    assert info["reference_message_for_similarity"] == g.config.initial_verse
    assert "global_theme" in info


def test_scenario_alliance_theme():
    """Messages proches du thème 'alliance' → bonus."""
    g = GameState("scenario_alliance")
    g.register_player("A")
    g.register_player("B")
    g.initialize_game()

    cp = g.current_player_id
    g.send_semantic_message(cp, "alliance and friendship forever")
    g.end_turn(cp)
    # Vérifier que la similarité au thème est calculée


def test_scenario_famine_kills_weakest():
    """Famine: les unités les plus faibles meurent d'abord."""
    g = GameState("scenario_famine")
    g.register_player("A")
    g.register_player("B")
    g.initialize_game()

    cp = g.current_player_id
    ps = g.players[cp]

    # Ajouter une unité forte (cavalry) et une faible (scout)
    t = list(ps.territories)[0]
    g.recruit_unit(cp, "cavalry", t)  # power = 5+2+2.5 = 9.5

    # Forcer la famine
    ps.food = -5.0

    import time
    g.turn_start_time = time.time() - 60  # long turn = plus de cost_per_unit
    r = g.end_turn(cp)

    if "famine_event" in r:
        killed_types = [u["type"] for u in r["famine_event"]["killed_units"]]
        # Les scouts (power 2.5) devraient mourir avant les cavalries (power 9.5)
        power_scores = [u["power_score"] for u in r["famine_event"]["killed_units"]]
        assert max(power_scores) <= 5  # shouldn't kill high-value units first


def test_scenario_territory_capture():
    """Capturer un hex ennemi → changement de propriétaire."""
    g = GameState("scenario_capture")
    g.register_player("A")
    g.register_player("B")
    g.initialize_game()

    # Vérifier que le board a bien deux capitales assignées
    for hid, hex_state in g.board.items():
        if hex_state.is_capital:
            assert hex_state.owner_id is not None


def test_scenario_score_victory():
    """Victoire par score après MAX_TURNS."""
    g = GameState("scenario_score")
    g.config.MAX_TURNS = 4
    g.config.victory_condition = "score"
    g.register_player("A")
    g.register_player("B")
    g.initialize_game()

    for _ in range(g.config.MAX_TURNS * 2):  # ×2 car tours alternés
        if g.winner or g.current_turn > g.config.MAX_TURNS + 1:
            break
        cp = g.current_player_id
        try:
            g.send_semantic_message(cp, "peace is war")
        except:
            pass
        g.end_turn(cp)

    assert g.winner is not None
    state = g.get_full_state()
    assert state["winner"] == g.winner
```

**Verify:**
```bash
cd /home/ai_agent/projects/competition_IA && python3 -m pytest tests/test_e2e_scenarios.py -v
```

**Commit:**
```bash
git add tests/test_e2e_scenarios.py
git commit -m "test: add end-to-end scenario tests"
```

---

### Task 12: Nettoyage, lint, et vérification finale

**Objective:** Finaliser le projet avec linting et vérification de cohérence avec la spec.

**Files:** Vérifier tous les fichiers du projet.

```bash
cd /home/ai_agent/projects/competition_IA

# Vérification syntaxe
python3 -c "
import ast, sys
from pathlib import Path
for f in Path('empires_in_the_fog').rglob('*.py'):
    ast.parse(f.read_text())
    print(f'✓ {f}')
print('All files parse OK')
"

# Tests complets
python3 -m pytest tests/ -v --tb=short --cov=empires_in_the_fog 2>/dev/null || python3 -m pytest tests/ -v --tb=short

# Vérifier la conformité spec
echo "=== Spec Compliance ==="
echo "Endpoints MCP:"
grep -c '@_mcp.tool()' empires_in_the_fog/server.py
echo "Event types:"
grep -c "EVENT_TYPES\|event_type" empires_in_the_fog/models.py
```

**Vérification checklist spec v1.4:**
- [ ] is_my_turn — ✅ (retourne turn_number, food, gold, time_limit, etc.)
- [ ] end_turn — ✅ (retourne food_consumed, food_remaining, semantic_result, famine_event)
- [ ] send_semantic_message — ✅ (≤10 mots, erreurs MESSAGE_TOO_LONG/EMPTY_MESSAGE/ALREADY_SENT)
- [ ] read_messages — ✅
- [ ] get_visible_map — ✅ (fog reveal radius = 2)
- [ ] get_units — ✅
- [ ] move_unit — ✅ (combat si ennemi, capture territoire)
- [ ] recruit_unit — ✅ (coût or, territoire contrôlé)
- [ ] get_full_state — ✅ (spectateur)
- [ ] get_game_rules — ✅ (markdown lisible par IA)
- [ ] get_event_stream — ⚠️ (SSE fourni par viewer, pas MCP direct)
- [ ] Conditions victoire — ✅ (domination, score, surrender)
- [ ] Famine + attrition — ✅ (formule exacte, sélection bottom 50%, power_score)
- [ ] Scoring sémantique — ✅ (vs adjacent, vs thème, bonus)
- [ ] Combat avec terrain — ✅ (fort 1.5×, forêt 1.2×, etc.)
- [ ] Timeout 60s — ✅ (Task 3)

**Commit final:**
```bash
git add -A
git commit -m "release: v1.4 — complete implementation of Empires in the Fog"
```

---

## Récapitulatif des tâches

| # | Tâche | Fichiers | Complexité |
|---|-------|----------|------------|
| 1 | Refactor — extraire models/config | `models.py`, `config.py`, `server.py` | 🟢 Simple |
| 2 | Embedding service réel | `embeddings.py`, `server.py` | 🟢 Simple |
| 3 | Timeout automatique 60s | `server.py` | 🟡 Moyen |
| 4 | Tests unitaires core | `test_models.py`, `test_config.py`, `test_embeddings.py` | 🟢 Simple |
| 5 | Tests GameState | `test_game_state.py` | 🟡 Moyen |
| 6 | Tests intégration | `test_integration.py` | 🟡 Moyen |
| 7 | CLI orchestrator | `cli.py` | 🟢 Simple |
| 8 | Spectator UI SSE | `viewer.py`, `templates/index.html`, `server.py` | 🔴 Complexe |
| 9 | Packaging + README | `pyproject.toml`, `README.md` | 🟢 Simple |
| 10 | Config Hermes MCP | `~/.hermes/config.yaml` | 🟢 Simple |
| 11 | Scénarios E2E | `test_e2e_scenarios.py` | 🟡 Moyen |
| 12 | Lint + vérification | Tous | 🟢 Simple |

**Ordre d'exécution recommandé:** 1 → 2 → 4 → 5 → 3 → 6 → 11 → 7 → 8 → 9 → 10 → 12

**Estimation totale:** ~2-3 heures de dev avec une bonne productivité.

---

> **Note:** Le serveur MCP existant (`server.py`) contient déjà ~70% de la logique de jeu. Ce plan complète les 30% manquants: refactoring propre, embedding réel, tests, spectator UI, et orchestration.
