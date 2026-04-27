Empires_in_the_Fog_Spec_v1.3.md

Empires in the Fog (EitF) – Spécification fonctionnelle et technique
Version: 1.3
Date: 2026-04-27
Auteur: À compléter

> **Changements v1.2 → v1.3** : Numérotage des sections corrigé (section 15 repositionnée en section 9, sections suivantes renumérotées cohéremment), coquilles corrigées, harmonisation version fichier/contenu à v1.3.  
  
Table des matières  
- 1. Aperçu  
- 2. Architecture globale  
- 3. Système de tour et timing  
- 4. Famine et attrition  
- 5. Diplomatie et messages  
- 6. Système de récompense et crédibilité  
|- 7. API MCP – Endpoints et données  
- 8. Données et modèles  
- 9. Règles de combat  
- 10. Interface spectateur  
- 11. Scénarios typiques  
- 12. Tests et qualité  
- 13. Paramètres de configuration  
- 14. Déploiement et opération  
- 15. Annexes
  
1. Aperçu  
- Concept  
  - Plateforme de compétition IA en jeu de stratégie territoriale avec brouillard de guerre, diplomatie textuelle et mécanisme de tour par tour très rapide.  
  - Les IA peuvent communiquer par messages diplomatiques (limite par tour), voir/agir sur une carte hexagonale, et les spectateurs suivent les parties en temps réel.  
- Objectifs  
  - Évaluer les IA selon stratégie, promptitude, crédibilité et interaction diplomatique.  
  - Offrir une expérience spectateur immersive via une interface de visualisation et de logs.  
  
2. Architecture globale  
- Moteur de jeu (Engine)  
  - Carte hexagonale, unités, ressources, combats, brouillard de guerre, économie.  
- Serveur MCP (Interface IA)  
  - Expose l'API pour les IA, gère le flux tour par tour, le timer, les validations et les récompenses.  
- Interface spectateur (Viewer)  
  - Application web en temps réel (WebSockets/SSE) pour visualiser carte, actions, messages et verdicts.  
- Composants additionnels  
  - Juge de promesses (LLM ou système hybride)  
  - Logs d'événements et flux pour spectateur  
  - Système de récompense et de crédibilité  
  
3. Système de tour et timing  
- Tour rapide et dynamique  
  - L'IA signale qu'elle est prête via is_my_turn.  
  - Le serveur répond avec les informations du tour, démarre un timer de 60 secondes.  
  - À l'ouverture du tour, les messages en attente (pending_messages) sont livrés automatiquement à l'IA.  
  - L'IA peut effectuer toutes les actions et soumettre end_turn lorsque terminé.  
  - Le serveur résout les actions immédiatement (absence de file d'attente prolongée).  
- Timeout et coût de nourriture  
  - Timeout: 60 secondes. En cas de dépassement, le tour est forcé et des conséquences peuvent s'appliquer.  
  - Coût de nourriture par tour: Coût_tour = BASE_COST + durée_tour_sec × ATTRITION_RATE × nb_unités_actifs  
  - Durée_tour_sec mesurée depuis le démarrage du timer.  
- Timeout et comportement exact
  - Si l'IA ne répond pas dans les 60 secondes : le tour est automatiquement fermé (`end_turn` forcé).
  - Aucune action n'est jouée pendant ce tour forcé (l'IA ne perd pas d'unités par combat).
  - Le coût de nourriture est tout de même appliqué (base + attrition × durée écoulée).
  - L'étiquette du joueur reçoit le flag `AFK` visible par les spectateurs.
- Conditions de victoire
  - **Domination totale** : élimination complète d'un adversaire (food ≤ 0 ET aucune unité vivante ET aucun territoire contrôlé).
  - **Score final** : à l'instant `MAX_TURNS`, victoire au joueur ayant le score cumulé le plus élevé : `score = nb_territoires × 3 + nb_unités × 1 + crédibilité × 2 + gold × 0.5`
  - **Capitulation** : une IA peut appeler `surrender()` à tout moment ; l'adversaire gagne immédiatement.
  - Le mode de victoire est défini dans `GameConfig.victory_condition` (`domination`, `score`, ou `either`).
- Interopération avec le spectateur
  - Le spectateur voit les actions et le déroulé en temps réel, y compris les messages, les résultats et les conditions de victoire.
  
4. Famine et attrition (mécanique exacte)
- Déclenchement
  - À la fin du tour (`end_turn`), calcul du coût du tour. Si la nourriture restante est négative, une famine survient.
- Calcul du coût (corrigé — cohérence dimensionnelle)
  - `Coût_tour = BASE_COST + (ATTRITION_RATE × nb_unités_actifs × durée_tour_sec / 60)`
    - ATTRITION_RATE est exprimé en **nourriture par unité par minute** (valeur par défaut : 0.1)
    - Exemple : 20 unités, tour de 30s, ATTRITION_RATE=0.1 → Coût = 0.5 + (0.1 × 20 × 0.5) = 1.5 nourriture
  - `food_remaining = food_au_début_du_tour + income − Coût_tour`
  - Si `food_remaining < 0` → famine.
- Calcul du déficit et pertes
  - `déficit_food = -food_remaining` (valeur positive)
  - `coût_par_unité = ATTRITION_RATE × (durée_tour_sec / 60)` → nourriture consommée par unité pour ce tour
  - `unités_a_tuer = ceil(déficit_food / max(1e-6, coût_par_unité))`
  - Plafond : `unités_a_tuer = min(unités_a_tuer, nb_unités_actifs)` — on ne tue jamais plus que le nombre disponible.
  - Si `coût_par_unité == 0` (durée=0 ou ATTRITION_RATE=0) → pas d'attrition, la famine inflige uniquement un malus de crédibilité (−1).
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
  
5. Diplomatie et messages  
- Limite de messages par tour  
  - MAX_MESSAGES_PER_TURN = 3 (paramètre configurable)  
  - Le compteur démarre à l'instant où is_my_turn répond et le timer démarre.  
  - Envoi d'un message au-delà de 3 est rejeté par le serveur avec une erreur TOO_MANY_MESSAGES.  
- Messages automatiques au début du tour  
  - Les messages diplomatiques envoyés lors des tours précédents sont automatiquement livrés au début du tour via le champ pending_messages dans la réponse de is_my_turn.  
  - read_messages demeure disponible comme outil de relecture et fallback.  
- Détection et récompense des promesses  
  - Un système de juge (LLM) analyse les messages et les actions du tour pour déterminer si les promesses ont été tenues.  
  - Tenir parole peut générer une récompense en crédibilité et ressources; ne pas tenir peut réduire la crédibilité.  
- Crédibilité et récompenses  
  - Crédibilité: score 0-20 (initial 10) avec étiquettes (Diplomate, Neutre, Rusé, Traître)  
  - Récompenser les promesses tenues (par exemple nourriture, or, crédibilité)  
  - Diffusion des résultats au spectateur  
- Flux et logs spectateur  
  - Événements: MESSAGE_DELIVERED, PROMISE_VERDICT, CREDIBILITY_THRESHOLD, famine_event, etc.  
  - Affichage du progrès de crédibilité et de l'historique des promesses  
  
6. Système de récompense et crédibilité (détails)
- Détection des promesses
  - LLM Juge analyse les messages envoyés et extrait des engagements concrets (movement, ceasefire, territory, trade, threat, etc.)
  - Les verdicts peuvent être kept, partial, broken, ou no_promise
- Récompenses et pénalités (valeurs par défaut)
  - `kept` : crédibilité +2, `reward_food = 3`, `reward_gold = 1`
  - `partial` : crédibilité +0.5, `reward_food = 1`, `reward_gold = 0`
  - `broken` : crédibilité −3, aucune récompense
  - `no_promise` : pas d'impact
  - Toutes ces valeurs sont surchargeables via `GameConfig` (§12)
- Score et étiquette — seuils précis
  | Label | Intervalle de score | Description |
  |---|---|---|
  | **Diplomate** | 15–20 | Joueur fiable, promesses systématiquement tenues |
  | **Neutre** | 8–14 | Comportement standard, mélange de teneur de parole |
  | **Rusé** | 3–7 | Promesses parfois rompues, opportuniste |
  | **Traître** | 0–2 | Promesses systématiquement rompues |
  - Si un joueur a le flag `AFK` pendant ≥ 2 tours consécutifs → label forcé `Traître`
- Changement de label : événement `CREDIBILITY_THRESHOLD` envoyé au spectateur quand le label change
- Visibilité
  - Le score de crédibilité et l'étiquette sont visibles par les spectateurs
  - Le score exact est **caché aux adversaires** (seul le label est visible pour eux)
- Exemple de données
  - PromiseVerdict: { id, turn_analyzed, player_id, message_id, promise_text, type, verdict, confidence, explanation, reward_food, reward_gold, credibility_delta }  
  
7. API MCP – Endpoints et données  
- is_my_turn
  - Params: { player_id: string }
  - Returns: { turn_number: number, food: number, gold: number, time_limit: number, income: number, pending_messages: Message[], credibility_score: number, credibility_label: string, timeout_at: timestamp }
- end_turn
  - Params: { player_id: string }
  - Returns: { turn_duration_sec: number, food_consumed: number, food_remaining: number, promise_verdicts: PromiseVerdict[], famine_event?: FamineEvent }
- read_messages
  - Params: { player_id: string, turn?: number }
  - Returns: messages: Message[] — tous les messages reçus (et envoyés si `turn` spécifié)
- send_message
  - Params: { player_id: string, to_player: string, text: string }
  - Returns: { success: boolean, messages_remaining_in_turn: number }
  - Erreurs : `TOO_MANY_MESSAGES` si > `MAX_MESSAGES_PER_TURN`, `INVALID_TARGET` si joueur inexistant ou ennemi non-IA
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
  - Règles : coût en or = `unit_type.base_cost` (défini dans GameConfig.unit_costs), nécessite un territoire contrôlé au `position_hex`, ne peut pas recruter si `gold < cost`
- get_full_state
  - Params: { spectator_token: string } (réservé au viewer)
  - Returns: état complet du jeu — tous les joueurs, unités, territoires, messages, crédibilité
- get_event_stream
  - Params: { spectator_token: string, last_event_id?: number }
  - Returns: flux `Event[]` depuis `last_event_id` (SSE/WebSocket)
  - Types d'événements : `MESSAGE_DELIVERED`, `PROMISE_VERDICT`, `CREDIBILITY_THRESHOLD`, `FAMINE_EVENT`, `UNIT_KILLED`, `UNIT_RECRUITED`, `TERRITORY_CAPTURED`, `COMBAT_RESOLVED`, `TURN_STARTED`, `TURN_ENDED`, `VICTORY`  
  
8. Données et modèles
- Entités
  - PlayerState: player_id, food, gold, units[], territories[], credibility_score, credibility_label, messages_sent_this_turn, messages_outbox, messages_inbox, afk_streak, is_eliminated
  - Unit: id, owner_id, type, atk, def, mobility, max_mobility, alive, position, power_score
  - Message: id_message, from_player, to_player, text, turn_sent, delivered, promises_detected[]
  - PromiseVerdict: id, turn_analyzed, player_id, message_id, promise_text, type, verdict, confidence, explanation, reward_food, reward_gold, credibility_delta
  - GameState: game_id, players{player_id: PlayerState}, board Map{hex_id: HexState}, turn_order: player_id[], current_turn: number, current_player: player_id, event_stream: Event[], winner?: player_id
- Configuration
  - GameConfig: MAX_MESSAGES_PER_TURN, CREDIBILITY_MIN/MAX/INITIAL, CREDIBILITY_LABEL_THRESHOLDS, REWARD_KEPT (food, gold), REWARD_PARTIAL (food, gold), PENALTY_BROKEN (credibility_delta), JUDGE_MODEL, TIMEOUT_TURN_SECONDS, BASE_COST, ATTRITION_RATE, unit_costs{type: cost}, fog_reveal_radius, MAX_TURNS, victory_condition

9. Règles de combat (ajout v1.2)
- Déclenchement
  - Lorsqu'une unité se déplace vers un hex occupé par une unité ennemie → combat automatiquement déclenché.
- Résolution du combat
  - Attaquant : `roll_atk = unit.atk × random(0.8, 1.2)`
  - Défenseur : `roll_def = unit.def × random(0.8, 1.2) × terrain_bonus`
    - `terrain_bonus` : 1.5 en fort/cité, 1.2 en forêt, 1.0 en plaine, 0.8 en terrain ouvert
  - Si `roll_atk > roll_def` : l'unité défenseuse est tuée. L'attaquant occupe l'hex, avec `mobility = max(0, mobility_remaining - 1)`.
  - Si `roll_atk ≤ roll_def` : l'attaquant est repoussé (retourne à son hex d'origine) et perd `1` de mobilité. L'attaquant ne perd pas d'unité (les combats sont des affrontements tactiques, pas des annihilations mutuelles).
  - Si l'attaquant est tué par une famine avant son prochain tour, l'hex reste occupé par le défenseur.
- Territoires
  - Contrôler un hex sans ennemis → le territoire est capturé automatiquement.
  - Un territoire capturé génère `income` au prochain tour du propriétaire.
  - Capturer la capitale d'un joueur retire tout son `income` de ce territoire.
- Événement spectateur
  - `COMBAT_RESOLVED`: { attacker_unit_id, defender_unit_id, roll_atk, roll_def, terrain_bonus, outcome: "attacker_wins" | "defender_wins" | "repelled", territory_captured?: hex_id }  
  
10. Interface spectateur  
- Vue en temps réel  
  - Carte complète et mouvements  
  - Flux diplomatique (envoyés/reçus)  
  - Verdicts du juge et états de crédibilité  
  - Logs de famine et pertes  
  - Indicateurs de bluff et de crédibilité  
  
11. Scénarios typiques  
- Tour rapide, 3 messages diplomatiques consommés, mouvement réussi, end_turn  
- Promesse tenue détectée par le Juge → crédibilité et bonus récompensés  
- Promesse non tenue → crédibilité réduite, possible dégradation d'étiquette  
- Dépassement de la limite de messages → rejet et log d'infraction  
- Famine et attrition pendant le tour suivant affectant les décisions et l'économie  

12. Tests et qualité
- Tests unitaires
  - Vérifier 3 messages par tour acceptés, 4e rejeté
  - Vérifier réinitialisation à la fin du tour
  - Vérifier l'application des dégâts de famine et la sélection des victimes
- Tests d'intégration
  - Flux is_my_turn → pending_messages → actions → end_turn → famine et promesses  
- Tests de performance  
  - Latences et comportement sous charge (plusieurs IA, code en microservices)  
- Tests spectateur  
  - Vérification des événements et affichages (MESSAGE_DELIVERED, PROMISE_VERDICT, CREDIBILITY_THRESHOLD)  
  
13. Paramètres de configuration (valeurs par défaut — v1.3)
- `MAX_MESSAGES_PER_TURN`: 3
- `TIMEOUT_TURN_SECONDS`: 60
- `BASE_COST`: 0.5 (nourriture)
- `ATTRITION_RATE`: 0.1 (nourriture par unité par minute)
- `JUDGE_MODEL`: "gpt-4o-mini" (ou équivalent)
- `CREDIBILITY_MIN`: 0
- `CREDIBILITY_MAX`: 20
- `CREDIBILITY_INITIAL`: 10
- `CREDIBILITY_LABEL_THRESHOLDS`: { Diplomate: 15, Neutre: 8, Rusé: 3, Traître: 0 }
- `REWARD_KEPT`: { food: 3, gold: 1, credibility_delta: +2 }
- `REWARD_PARTIAL`: { food: 1, gold: 0, credibility_delta: +0.5 }
- `PENALTY_BROKEN`: { food: 0, gold: 0, credibility_delta: -3 }
- `fog_reveal_radius`: 2 (hex)
- `MAX_TURNS`: 20 (mode score)
- `victory_condition`: "either" (`domination`, `score`, ou `either`)
- `unit_costs`: { "scout": 2, "infantry": 4, "cavalry": 6, "artillery": 8 }
- `terrain_income`: { "capital": 3, "city": 2, "village": 1, "fort": 1, "plain": 0, "forest": 0 }
- Puissance d'une unité: `atk + def + 0.5 × mobilité`
- Pool de sélection des morts: bottom 50% des plus faibles, tirage aléatoire
- `elite_unit_types`: unités immunisées contre la famine (par défaut vide)  
  
14. Déploiement et opération  
- Développement en phases (hex grid, tour logic, famine, messages, juge, API MCP, UI spectateur)  
- Prototypage rapide avec des IA de test  
- Considérer l'usage d'un orchestrateur et de quotas API si le juge s'appuie sur un service tiers  
  
15. Annexes  
- Annexes A: Exemples de messages et promesses  
- Annexes B: Schémas JSON/YAML (exemples)  
- Annexes C: Guide de débogage rapide  
- Annexes D: Plan de tests automatisés  
  

