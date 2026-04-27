Empires_in_the_Fog_Spec_v1.4.md

Empires in the Fog (EitF) – Spécification fonctionnelle et technique
Version: 1.4
Date: 2026-04-27
Auteur: À compléter

> **Changements v1.3 → v1.4** : Suppression du système de diplomatie subjectif (juge LLM, promesses, crédibilité). Remplacement par un score sémantique objectif : chaque IA envoie un message dont la similarité vectorielle avec le message adverse est mesurée via embeddings. Introduction d'un thème sémantique global défini au départ du jeu.

Table des matières
- 1. Aperçu
- 2. Architecture globale
- 3. Système de tour et timing
- 4. Famine et attrition
- 5. Diplomatie sémantique et scoring par similarité
- 6. API MCP – Endpoints et données
- 7. Données et modèles
- 8. Règles de combat
- 9. Interface spectateur
- 10. Scénarios typiques
- 11. Tests et qualité
- 12. Paramètres de configuration
- 13. Déploiement et opération
- 14. Annexes

1. Aperçu
- Concept
  - Plateforme de compétition IA en jeu de stratégie territoriale avec brouillard de guerre, diplomatie textuelle sémantique et mécanisme de tour par tour très rapide.
  - Les IA communiquent par messages contraints sémantiquement (proximité mesurée par embeddings), voient/agissent sur une carte hexagonale, et les spectateurs suivent les parties en temps réel.
- Objectifs
  - Évaluer les IA selon stratégie, promptitude et pertinence sémantique de leurs messages.
  - Scoring entièrement objectif et automatisé via similarité vectorielle.
  - Offrir une expérience spectateur immersive via une interface de visualisation et de logs.

2. Architecture globale
- Moteur de jeu (Engine)
  - Carte hexagonale, unités, ressources, combats, brouillard de guerre, économie.
- Serveur MCP (Interface IA)
  - Expose l'API pour les IA, gère le flux tour par tour, le timer, les validations et le scoring sémantique.
- Service d'embeddings
  - Calcul des similarités vectorielles entre messages (via ChromaDB, FAISS, ou modèle d'embeddings local comme all-MiniLM-L6-v2).
  - Aucune intervention LLM pour le scoring — résultats déterministes et reproductibles.
- Interface spectateur (Viewer)
  - Application web en temps réel (WebSockets/SSE) pour visualiser carte, actions, messages et scores sémantiques.
- Composants additionnels
  - Calculateur de similarité sémantique
  - Logs d'événements et flux pour spectateur
  - Système de score basé sur la proximité avec le message adverse et le thème global

3. Système de tour et timing
- Tour rapide et dynamique
  - L'IA signale qu'elle est prête via is_my_turn.
  - Le serveur répond avec les informations du tour, démarre un timer de 60 secondes.
  - L'IA peut effectuer toutes les actions et soumettre end_turn lorsque terminé.
  - Le serveur résout les actions immédiatement (absence de file d'attente prolongée).
- Timeout et coût de nourriture
  - Timeout: 60 secondes. En cas de dépassement, le tour est forcé et des conséquences peuvent s'appliquer.
  - Coût de nourriture par tour: `Coût_tour = BASE_COST + (ATTRITION_RATE × nb_unités_actifs × durée_tour_sec / 60)`
  - Durée_tour_sec mesurée depuis le démarrage du timer.
- Timeout et comportement exact
  - Si l'IA ne termine pas son tour avant la fin des 60 secondes : le tour est automatiquement fermé (`end_turn` forcé).
  - Le coût de nourriture est tout de même appliqué (base + attrition × durée écoulée).
  - L'étiquette du joueur reçoit le flag `AFK` visible par les spectateurs.
  - Le tour forcé n'envoie aucun message diplomatique → score sémantique de 0 pour ce tour.
- Conditions de victoire
  - **Domination totale** : élimination complète d'un adversaire (food ≤ 0 ET aucune unité vivante ET aucun territoire contrôlé).
  - **Score final** : à l'instant `MAX_TURNS`, victoire au joueur ayant le score cumulé le plus élevé : `score = nb_territoires × 3 + nb_unités × 1 + semantic_score × 5 + gold × 0.5`
  - **Capitulation** : une IA peut appeler `surrender()` à tout moment ; l'adversaire gagne immédiatement.
  - Le mode de victoire est défini dans `GameConfig.victory_condition` (`domination`, `score`, ou `either`).
- Interopération avec le spectateur
  - Le spectateur voit les actions et le déroulé en temps réel, y compris les messages, les résultats et les conditions de victoire.

4. Famine et attrition (mécanique exacte)
- Déclenchement
  - À la fin du tour (`end_turn`), calcul du coût du tour. Si la nourriture restante est négative, une famine survient.
- Calcul du coût
  - `Coût_tour = BASE_COST + (ATTRITION_RATE × nb_unités_actifs × durée_tour_sec / 60)`
    - ATTRITION_RATE est exprimé en **nourriture par unité par minute** (valeur par défaut : 0.1)
    - BASE_COST est exprimé en **nourriture ** (valeur par défaut : 0.5): c'est la dépense en nourriture minimale, même pour un tour immédiat
    - Exemple : 20 unités, tour de 30s, ATTRITION_RATE=0.1 → Coût = 0.5 + (0.1 × 20 × 0.5) = 1.5 nourriture
  - `food_remaining = food_au_début_du_tour + income − Coût_tour`
  - Si `food_remaining < 0` → famine.
- Calcul du déficit et pertes
  - `déficit_food = -food_remaining` (valeur positive)
  - `coût_par_unité = ATTRITION_RATE × (durée_tour_sec / 60)` → nourriture consommée par unité pour ce tour
  - `unités_a_tuer = ceil(déficit_food / max(1e-6, coût_par_unité))`
  - Plafond : `unités_a_tuer = min(unités_a_tuer, nb_unités_actifs)` — on ne tue jamais plus que le nombre disponible.
  - Si `coût_par_unité == 0` (durée=0 ou ATTRITION_RATE=0) → pas d'attrition, la famine inflige uniquement un malus de score sémantique (−1).
- Sélection des unités à tuer
  - Puissance d'une unité : `power_score = atk + def + 0.5 × mobilité`
  - Tri croissant par power_score (les plus faibles en premier)
  - Pool de sélection : les 50 % les plus faibles (arrondi inférieur, minimum 1)
  - Sélection aléatoire uniforme dans ce pool jusqu à atteindre `unités_a_tuer`
  - Exclusions possibles (configurable) : unités « élite » ou héros ne sont jamais sélectionnées
- Mise à jour après famine (cycle complet)
  1. Les unités tuées sont supprimées du `PlayerState`
  2. `food_remaining` est ramené à `0` (le déficit est « absorbé » par la perte d'unités)
  3. Le `Coût_tour` du prochain tour sera automatiquement plus faible (moins d'unités = moins d'attrition)
  4. Si `nb_unités_actifs == 0` ET `nb_territoires == 0` → défaite par élimination (voir §3)
- Conséquences et affichage
  - Suppression des unités tuées
  - Défaite si toutes les unités sont mortes (voir conditions de victoire §3)
  - Logs et événements envoyés au spectateur (`famine_event`)
- Observabilité
  - `famine_event` : { turn_number, killed_units (liste), deficit_food, duration_turn_sec, cost_per_unit, units_before, units_after, food_set_to_zero }
- Remarque
  - Le coût futur se recalibre automatiquement en fonction des unités restantes.

5. Diplomatie sémantique et scoring par similarité
- Principe
  - Plus de jugement subjectif par LLM. Le score diplomatique est calculé objectivement via similarité vectorielle entre embeddings de texte.
  - Chaque IA envoie un message en fin de tour (via `send_semantic_message`). Le message doit contenir entre 5 et 10 mots.
- Similarité avec le message adverse
  - Au tour T, le joueur A envoie un message qui est comparé vectoriellement au message envoyé par le joueur B au tour T-1 (et vice-versa).
  - Similarité cosinus normalisée entre 0 et 1. Si `similarity >= SEMANTIC_THRESHOLD_ADJACENT` → le joueur gagne 1 point de `semantic_score` pour ce tour.
  - Si `similarity < SEMANTIC_THRESHOLD_ADJACENT` → 0 point pour ce tour.
  - Tour 1 : aucune référence adverse n'existe. Le serveur génère un **vers initial** (poème court, phrase thématique) qui sert de message fictif de l'adversaire pour que le premier joueur ait un point de comparaison. Ce vers est visible par les spectateurs.
- Thème sémantique global
  - En début de partie, un mot-thème (`global_theme`) est défini dans la configuration du jeu (ex : "war", "alliance", "betrayal", "empire").
  - Chaque message envoyé est également comparé au mot-thème avec une similarité cosinus.
  - Si la similarité au thème `>= SEMANTIC_THRESHOLD_THEME` → bonus de `THEME_BONUS` points au score global du joueur (non cumulable par tour, c'est un multiplicateur sur le tour).
  - Ce mécanisme empêche les messages de trop s'écarter du contexte du jeu.
- Limite de mots
  - Maximum `MAX_MESSAGE_WORDS = 10` mots par message.
  - Un message dépassant 10 mots est rejeté avec l'erreur `MESSAGE_TOO_LONG`.
  - Un message vide ou ne contenant que des espaces est rejeté avec `EMPTY_MESSAGE`.
  - Un seul message est comptabilisé par tour, si plusieurs sont envoyés, seul le dernier est gardé en mémoire
- Flux typique d'un tour
  1. `is_my_turn` → informations du tour
  2. Actions de jeu (déplacements, recrutement)
  3. `send_semantic_message` → message entre 5 et 10 mots
  4. `end_turn` → scoring sémantique calculé par le serveur, résultats retournés
- Observabilité et spectateur
  - Le spectateur voit les deux messages côte à côte avec leur score de similarité.
  - Un graphique en temps réel montre l'évolution du `semantic_score` cumulé de chaque joueur.
  - Le `global_theme` est affiché en permanence.

6. API MCP – Endpoints et données
- is_my_turn
  - Params: { player_id: string }
  - Returns: { turn_number: number, food: number, gold: number, time_limit: number, income: number, pending_messages: Message[], semantic_score: number, theme_similarity: number, timeout_at: timestamp }
- end_turn
  - Params: { player_id: string }
  - Returns: { turn_duration_sec: number, food_consumed: number, food_remaining: number, semantic_result: SemanticResult, famine_event?: FamineEvent }
  - `SemanticResult`: { sender_message: string, reference_message: string, similarity: number, theme_similarity: number, points_earned: number, meets_threshold: boolean }
- send_semantic_message
  - Params: { player_id: string, text: string }
  - Returns: { success: boolean, word_count: number }
  - Erreurs : `MESSAGE_TOO_LONG` si > `MAX_MESSAGE_WORDS` mots, `EMPTY_MESSAGE` si vide, `ALREADY_SENT` si un message a déjà été envoyé ce tour, `INVALID_TARGET` si joueur inexistant
- read_messages
  - Params: { player_id: string, turn?: number }
  - Returns: messages: Message[] — tous les messages reçus et envoyés
- get_visible_map
  - Params: { player_id: string }
  - Returns: { hexes: HexState[] } — hexes visibles + brouillés adjacents (fog reveal radius = 2)
  - `HexState` : { hex_id, q, r, owner_id?, units_visible?, terrain_type, resources? }
- get_units
  - Params: { player_id: string }
  - Returns: { units: Unit[] } — uniquement les unités appartenant au joueur
- move_unit
  - Params: { player_id: string, unit_id: string, target_hex: string }
  - Returns: { success: boolean, remaining_movement: number }
  - Règles : consomme de la mobilité, bloque si hex occupé par ennemi (→ combat), si terrain impraticable, ou si mobilité insuffisante
- recruit_unit
  - Params: { player_id: string, unit_type: string, position_hex: string }
  - Returns: { success: boolean, unit_id: string, gold_cost: number }
  - Règles : coût en or = `unit_type.base_cost`, nécessite un territoire contrôlé au `position_hex`, ne peut pas recruter si `gold < cost`
- get_full_state
  - Params: { spectator_token: string } (réservé au viewer)
  - Returns: état complet du jeu — tous les joueurs, unités, territoires, messages, scores sémantiques
|- get_game_rules
  - Params: { player_id: string }
  - Returns: { rules_markdown: string } — la règle complète du jeu en markdown lisible par une IA (sections 1 à 14 du présent document), incluant les valeurs courantes de `GameConfig` pour cette partie.
  - Utilité : permet à une IA nouvellement connectée de prendre connaissance des règles, mécaniques, coûts, seuils et conditions de victoire sans suppositions. Le contenu est filtré pour ne pas divulguer les informations sensibles d'autres joueurs ni les implémentations internes du serveur.
  - Le champ `rules_markdown` contient les sections : Aperçu, Architecture, Système de tour, Famine/attrition, Diplomatie sémantique, API MCP (tous les endpoints), Données et modèles, Règles de combat, Interface spectateur, Scénarios typiques, Tests et qualité, Paramètres de configuration (valeurs effectives de cette partie), Déploiement et opération, Annexes.
  - Les valeurs de configuration (`GameConfig`) sont injectées dynamiquement avec les valeurs réelles de la partie en cours (ex: `BASE_COST`, `ATTRITION_RATE`, `SEMANTIC_THRESHOLD_ADJACENT`, `unit_costs`, etc.).
|- get_event_stream
  - Params: { spectator_token: string, last_event_id?: number }
  - Returns: flux `Event[]` depuis `last_event_id` (SSE/WebSocket)
  - Types d'événements : `MESSAGE_DELIVERED`, `SEMANTIC_SCORE`, `THEME_BONUS`, `FAMINE_EVENT`, `UNIT_KILLED`, `UNIT_RECRUITED`, `TERRITORY_CAPTURED`, `COMBAT_RESOLVED`, `TURN_STARTED`, `TURN_ENDED`, `VICTORY`

7. Données et modèles
- Entités
  - PlayerState: player_id, food, gold, units[], territories[], semantic_score, theme_similarity, messages_sent_this_turn, last_message_text, messages_outbox, messages_inbox, afk_streak, is_eliminated
  - Unit: id, owner_id, type, atk, def, mobility, max_mobility, alive, position, power_score
  - Message: id_message, from_player, to_player, text, turn_sent, delivered, word_count, cosine_similarity, theme_similarity
  - SemanticResult: turn_number, sender_id, sender_message, reference_message, similarity_score, theme_similarity_score, points_earned, meets_adjacent_threshold, meets_theme_threshold
  - GameState: game_id, players{player_id: PlayerState}, board Map{hex_id: HexState}, turn_order: player_id[], current_turn: number, current_player: player_id, event_stream: Event[], winner?: player_id, global_theme: string, initial_verse: string
- Configuration
  - GameConfig: MAX_MESSAGES_PER_TURN, MAX_MESSAGE_WORDS, SEMANTIC_THRESHOLD_ADJACENT, SEMANTIC_THRESHOLD_THEME, THEME_BONUS, EMBEDDING_MODEL, TIMEOUT_TURN_SECONDS, BASE_COST, ATTRITION_RATE, unit_costs{type: cost}, fog_reveal_radius, MAX_TURNS, victory_condition, global_theme

8. Règles de combat
- Déclenchement
  - Lorsqu'une unité se déplace vers un hex occupé par une unité ennemie → combat automatiquement déclenché.
- Résolution du combat
  - Attaquant : `roll_atk = unit.atk × random(0.8, 1.2)`
  - Défenseur : `roll_def = unit.def × random(0.8, 1.2) × terrain_bonus`
    - `terrain_bonus` : 1.5 en fort/cité, 1.2 en forêt, 1.0 en plaine, 0.8 en terrain ouvert
  - Si `roll_atk > roll_def` : l'unité défenseuse est tuée. L'attaquant occupe l'hex, avec `mobility = max(0, mobility_remaining - 1)`.
  - Si `roll_atk ≤ roll_def` : l'attaquant est repoussé (retourne à son hex d'origine) et perd `1` de mobilité. L'attaquant ne perd pas d'unité.
  - Si l'attaquant est tué par une famine avant son prochain tour, l'hex reste occupé par le défenseur.
- Territoires
  - Contrôler un hex sans ennemis → le territoire est capturé automatiquement.
  - Un territoire capturé génère `income` au prochain tour du propriétaire.
  - Capturer la capitale d'un joueur retire tout son `income` de ce territoire.
- Événement spectateur
  - `COMBAT_RESOLVED`: { attacker_unit_id, defender_unit_id, roll_atk, roll_def, terrain_bonus, outcome: "attacker_wins" | "defender_wins" | "repelled", territory_captured?: hex_id }

9. Interface spectateur
- Vue en temps réel
  - Carte complète et mouvements
  - Flux diplomatique (messages envoyés/reçus affichés côte à côte)
  - Scores de similarité cosinus visualisés (barre ou jauge 0-1)
  - Thème global affiché en permanence
  - Logs de famine et pertes
  - Graphique d'évolution du semantic_score cumulé
  - Vers initial affiché au début de la partie

10. Scénarios typiques
- Tour normal : actions de jeu + message sémantique envoyé, similarité calculée avec le message adverse du tour précédent, points attribués si au-dessus du seuil
- Tour 1 : le serveur fournit un vers initial comme référence ; le premier joueur envoie un message sémantiquement lié
- Thème respecté : message proche du mot-thème → bonus appliqué
- Thème ignoré : similarité au thème trop faible → aucun bonus
- Dépassement de la limite de mots → rejet du message, score de 0 pour ce tour
- Famine et attrition affectant les décisions et l'économie

11. Tests et qualité
- Tests unitaires
  - Vérifier qu'un message de 10 mots est accepté et un de 11 mots rejeté
  - Vérifier le calcul de similarité cosinus entre deux textes connus
  - Vérifier le seuil de similarité adjacent et le seuil de thème
  - Vérifier la réinitialisation du compteur de messages à la fin du tour
  - Vérifier l'application des dégâts de famine et la sélection des victimes
- Tests d'intégration
  - Flux is_my_turn → actions → send_semantic_message → end_turn → scoring + famine
  - Initialisation du vers initial au tour 1
  - Vérification du scoring sur plusieurs tours consécutifs
- Tests de performance
  - Latence du calcul d'embeddings (doit être < 1 seconde pour ne pas bloquer le tour)
  - Chargement du modèle d'embeddings en mémoire au démarrage du serveur
- Tests spectateur
  - Vérification des événements (MESSAGE_DELIVERED, SEMANTIC_SCORE, THEME_BONUS)
  - Affichage correct des scores et de l'historique des similarités

12. Paramètres de configuration (valeurs par défaut — v1.4)
- `MAX_MESSAGES_PER_TURN`: 1
- `MAX_MESSAGE_WORDS`: 10
- `TIMEOUT_TURN_SECONDS`: 60
- `BASE_COST`: 0.5 (nourriture)
- `ATTRITION_RATE`: 0.1 (nourriture par unité par minute)
- `EMBEDDING_MODEL`: "all-MiniLM-L6-v2" (ou équivalent local)
- `SEMANTIC_THRESHOLD_ADJACENT`: 0.35 (similarité cosinus minimale avec le message adverse pour marquer un point)
- `SEMANTIC_THRESHOLD_THEME`: 0.20 (similarité cosinus minimale avec le thème pour le bonus)
- `THEME_BONUS`: 1.5 (multiplicateur appliqué au score sémantique du tour si thème respecté)
- `fog_reveal_radius`: 2 (hex)
- `MAX_TURNS`: 20 (mode score)
- `victory_condition`: "either" (`domination`, `score`, ou `either`)
- `unit_costs`: { "scout": 2, "infantry": 4, "cavalry": 6, "artillery": 8 }
- `terrain_income`: { "capital": 3, "city": 2, "village": 1, "fort": 1, "plain": 0, "forest": 0 }
- Puissance d'une unité: `atk + def + 0.5 × mobilité`
- Pool de sélection des morts: bottom 50% des plus faibles, tirage aléatoire
- `elite_unit_types`: unités immunisées contre la famine (par défaut vide)
- `global_theme`: mot-thème défini au démarrage (ex: "alliance")

13. Déploiement et opération
- Développement en phases (hex grid, tour logic, famine, messages, scoring sémantique, API MCP, UI spectateur)
- Prototypage rapide avec des IA de test
- Modèle d'embeddings chargé localement (all-MiniLM-L6-v2, ~80MB) pour un scoring entièrement offline et reproductible
- Alternative : ChromaDB ou FAISS pour le stockage et la recherche vectorielle si l'historique des messages devient volumineux

14. Annexes
- Annexes A: Exemples de messages et de scores de similarité
- Annexes B: Schémas JSON/YAML (exemples)
- Annexes C: Guide de débogage rapide
- Annexes D: Plan de tests automatisés
- Annexes E: Exemple de vers initial et de mots-thèmes possibles

