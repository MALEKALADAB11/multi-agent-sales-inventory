# Backlog complet du projet — Sprint 0 → aujourd'hui

Généré le 2026-07-05 à partir de l'historique git réel + mémoire de session. Destiné à être importé manuellement dans le GitHub Project **PFE-2026-G1/Backlog Sales**. Voir aussi `docs/BACKLOG.csv` (format Titre/Statut, prêt à coller dans la vue Table).

Convention statut : `Done` = terminé et vérifié · `To Do` = feuille de route non exécutée.

---

## Sprint 0 — Fondations (structure initiale, agents v1)
- [x] Structure modulaire complète (sales-module + inventory-module)
- [x] Premier agent analyste (forecasting ARIMA + visualisation workflow)
- [x] Agent Analyste LangGraph + WebSocket + mock dynamique
- [x] Fusion des agents analyse vente & analyse stock
- [x] WebSocket stock (premier câblage temps réel)
- [x] Agent Stratège + CoachAgent LLM + météo réelle + scraper Ooredoo
- [x] Correction USE_LLM / chemins data/raw / initial_state kwargs / garde WebSocket
- [x] Données réelles Ooredoo I63 (CSV/XLS, mock_provider)
- [x] Connexion inventory-module aux données réelles
- [x] Login / authentification
- [x] Agent Coach + RAG chat (premier jet)
- [x] Monitoring (dashboard initial)
- [x] Fix schémas DB sales/inventory/monitoring (première vague)
- [x] Context Agent ajouté + amélioration Agent Analyste

## Sprint 1 — Données séries temporelles (historique 3 ans)
- [x] Rebuild `inventory.sales_history` en journalier (1,49M lignes, 4,5 ans, Jan 2022 → Jul 2026)
- [x] Backfill synthétique réaliste 2022-2024 (`seed_3years_history.py`)
- [x] Colonnes enrichies saisonnières (`promo_type`, `day_of_week`, `is_event_day`, `uplift_factor`, etc.)
- [x] `get_seasonal_demand_profile()` — profil demande par mois/événement/saison/jour de semaine

## Sprint 2 — Production readiness UI/Coach (2026-06-29)
- [x] Dashboard UI — carte "Plan Stratégique" + section Coaching Cards
- [x] Coach streaming SSE (`/api/v1/coach/stream`) + parsing frontend natif `fetch()`/`ReadableStream`
- [x] Seed RAG Milvus — 200+ scripts de vente Ooredoo (10 catégories)
- [x] Fallback SQL du forecast (`_sql_rolling_forecast`) si TimesFM échoue

## Sprint 3 — Intégration stock ↔ urgence vente (2026-06-29)
- [x] Outil ReAct `get_stock_alerts` (7e outil analyste)
- [x] Enrichissement urgence avec `stock_urgency_boost`
- [x] Profil conseiller enrichi (top produits, ancienneté, cache 5 min)
- [x] Cache forecast Redis (`forecast_eod:{store}:{date}`, TTL 15 min)
- [x] Index PostgreSQL de performance (8 index CONCURRENTLY)

## Sprint 4 — HITL, sécurité, tests (2026-06-29)
- [x] Backend HITL (`hitl_router.py`, table `public.hitl_reviews`, endpoints pending/validate/stats)
- [x] Panel Angular HITL (slide-out, badge rouge, polling 30s)
- [x] Rate limiting (`slowapi`) sur `/chat` et `/stream`
- [x] Tests unitaires analyste, HITL, intent coach
- [x] Nœud `_constraints_check_node` (budget cap, MOQ, stockout-avant-arrivée, EOL)

## Sprint 5 — Guardrail + architecture agentique unifiée (2026-06-30)
- [x] Agent Guardrail/Critic — 7 règles (G1-G7), 4 verdicts (APPROVE/REWRITE/ESCALATE/BLOCK)
- [x] Cross-domain scoring produit (`score_product`, 6 critères pondérés)
- [x] `RetailState` unifié (TypedDict consolidé tous domaines)
- [x] `SupervisorAgent` LangGraph — 12 nœuds, dispatch parallèle 4 branches
- [x] Préchargement TimesFM/Prophet au démarrage

## Sprint 6 — Câblage guardrail + RBAC (2026-06-30)
- [x] Guardrail câblé dans `/chat` (BLOCK/ESCALATE réels)
- [x] `scored_products` exposés dans la réponse `/chat`
- [x] RBAC store-level (`validate_store_access`, manager vs vendeur)
- [x] Fix URL prod frontend (`window.location.origin`)
- [x] 34 tests guardrail

## Sprint 7 — UX temps réel + E2E (2026-06-30)
- [x] Badge guardrail dans le chat UI (BLOCK/ESCALATE/REWRITE)
- [x] Événements guardrail via WebSocket (`guardrailEvent`, `guardrailHistory`)
- [x] Responsive mobile complet (chat, conseiller, inventory)
- [x] Tests E2E Playwright (5 suites : auth, dashboard, chat, inventory, mobile)
- [x] Flux WebSocket alertes inventaire (`inventory_alerts`)

## Sprint 8 — Durcissement production (2026-06-30)
- [x] `SupervisorAgent` exposé via API (`/api/v1/supervisor/run|async|status`)
- [x] CI/CD GitHub Actions (backend-tests, frontend-tests, e2e)
- [x] Chips `scored_products` dans le chat UI
- [x] Panel historique guardrail dans le monitoring
- [x] Tests unitaires Angular (dashboard, chat, websocket)
- [x] Tests backend coach_chat (~25 tests)

## Sprint 9 — Finalisation & polissage (2026-07-01)
- [x] Responsive mobile complet (sidebar slide-in, hamburger, overlay)
- [x] Loading skeletons Dashboard (shimmer, pas de zéros pendant chargement)
- [x] Auto-refresh token JWT (10 min)
- [x] Bannière offline / error boundary
- [x] Fix `sales.transactions` → `sales.transactions_rt` dans cross_domain_tools
- [x] Nettoyage `__pycache__` du repo git

## Sprint 10 — Agent Analyste séries temporelles robuste (2026-07-01)
- [x] 4 nouveaux outils ReAct : détection d'anomalies (z-score), décomposition STL-like, prévision multi-horizon, vélocité produit
- [x] Fix bug statistique critique AVG vs SUM (benchmark z-score)
- [x] Séparation claire `transactions` (historique fiable) vs `transactions_rt` (simulé)
- [x] System prompt ReAct v3 — 11 outils documentés

## Sprint 11 — Kanban achats + fondation data + temps réel visible (2026-07-03 → 2026-07-05, cette session)
- [x] Board Kanban achats (`/purchase-board`) — colonnes Brouillon→Soumis→Confirmé→Expédié→Reçu+Problèmes, drag-and-drop CDK
- [x] Backend `supply_routes.py` + `supply_repo.py` — cycle de vie complet des commandes fournisseur
- [x] Fix bug corruption stock — trigger `trg_sync_stock_on_reco` supprimé (une recommandation IA gonflait le stock physique avant approbation)
- [x] Fix N+1 — fusion des 6 requêtes de `get_seasonal_demand_profile` en 2
- [x] Verrous cache météo/jours fériés (course entre threads corrigée)
- [x] 8 index PostgreSQL manquants corrigés et appliqués
- [x] Dédoublonnage recommandations "pending" (509→30) + index unique partiel
- [x] Contraintes CHECK sur `alerts.status` / `recommendations.status`
- [x] 8 clés étrangères manquantes ajoutées (alerts, recommendations, purchase_orders, stock_movements)
- [x] Fix bug critique `_DataCache.record_sale` manquant — chemin temps réel vente→stock cassé silencieusement depuis toujours
- [x] Ticker "Ventes en direct" temps réel sur Dashboard (WebSocket, pas de polling)
- [x] Badge "LIVE" honnête sur `/inventory` (branché sur l'état réel de connexion)
- [x] `docs/DATA_ARCHITECTURE.md` — diagramme ER Mermaid + documentation conceptuelle complète

---

## Backlog restant (feuille de route non exécutée)

### Phase 2 — Unification propagation temps réel
- [ ] Retirer `sales-module/api/websocket_endpoint.py` (confirmé probablement mort)
- [ ] Brancher un abonné réel sur le bus Redis d'alertes (`AsyncAlertListener` jamais instancié)
- [ ] Alimenter le chemin rapide Redis du Coach (`publish_inventory_snapshot()` jamais appelé)
- [ ] Recalcul ciblé par SKU (au lieu de tout le store) en temps réel

### Phase 3 — Kanban alertes/recommandations + honnêteté frontend
- [ ] Board Kanban sibling pour alertes/recommandations (précondition dédoublonnage déjà faite)
- [ ] Abonnement live du Coach Chat à l'inventaire (au lieu du transfert ponctuel sessionStorage)

### Dette technique identifiée (non urgente)
- [ ] Consolider les 17-21 implémentations d'accès DB dupliquées (pooling incohérent)
- [ ] Unifier les 5 conventions de variables d'environnement DB
- [ ] Retirer le code mort confirmé (`monitoring-module/` mock, `postgres_repo.py` ciblant `asc_db`, configs Alembic mortes)
- [ ] Fusionner les deux `agent_logger.py` divergents (root + sales-module)
- [ ] Décider de la source de vérité des migrations (SQL + runner vs Alembic re-baselineé)
