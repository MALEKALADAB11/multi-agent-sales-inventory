# Backlog complet du projet — Sprint 0 → aujourd'hui

## Sprint 0 — Fondations
- [x] Structure modulaire sales-module + inventory-module
- [x] Agent Analyste (vente) LangGraph + WebSocket
- [x] AnalysisAgent inventaire (fetch → compute → reason)
- [x] ContextAgent inventaire (events, promos, saisonnalité)
- [x] DecisionAgent inventaire (recommandations actionnables)
- [x] InventoryOrchestrator (batch parallélisé, 8 workers)
- [x] Agent Stratège + CoachAgent LLM + météo + scraper Ooredoo
- [x] Données réelles Ooredoo I63 (CSV/XLS)
- [x] Login / authentification
- [x] Agent Coach + RAG chat
- [x] Monitoring (dashboard initial)
- [x] Fix schémas DB sales/inventory/monitoring

## Sprint 1 — Données séries temporelles
- [x] Rebuild sales_history journalier (1.49M lignes, 4.5 ans)
- [x] Backfill synthétique 2022-2024
- [x] get_seasonal_demand_profile() (agents inventaire)

## Sprint 2 — UI/Coach
- [x] Dashboard UI (Plan Stratégique, Coaching Cards)
- [x] Coach streaming SSE
- [x] Seed RAG Milvus (200+ scripts)
- [x] Fallback SQL du forecast

## Sprint 3 — Stock ↔ urgence vente
- [x] Outil ReAct get_stock_alerts
- [x] Enrichissement urgence stock_urgency_boost
- [x] Profil conseiller enrichi
- [x] Cache forecast Redis
- [x] Index PostgreSQL performance

## Sprint 4 — HITL, sécurité, tests
- [x] Backend + panel Angular HITL
- [x] Rate limiting slowapi
- [x] Tests unitaires agents
- [x] constraints_check_node (DecisionAgent inventaire)

## Sprint 5 — Guardrail + architecture unifiée
- [x] Agent Guardrail/Critic (7 règles)
- [x] Cross-domain scoring produit
- [x] RetailState unifié
- [x] SupervisorAgent LangGraph (12 nœuds)
- [x] Préchargement TimesFM/Prophet

## Sprint 6 — Câblage guardrail + RBAC
- [x] Guardrail câblé dans /chat
- [x] scored_products dans /chat
- [x] RBAC store-level
- [x] Fix URL prod frontend
- [x] Tests guardrail

## Sprint 7 — UX temps réel + E2E
- [x] Badge guardrail chat UI
- [x] Événements guardrail WebSocket
- [x] Responsive mobile
- [x] Tests E2E Playwright
- [x] WS alertes inventaire

## Sprint 8 — Durcissement production
- [x] SupervisorAgent exposé via API
- [x] CI/CD GitHub Actions
- [x] Panel historique guardrail monitoring
- [x] Tests unitaires Angular + backend coach_chat

## Sprint 9 — Finalisation
- [x] Responsive mobile complet
- [x] Loading skeletons Dashboard
- [x] Auto-refresh token JWT
- [x] Bannière offline
- [x] Fix transactions -> transactions_rt

## Sprint 10 — Agent Analyste (vente) séries temporelles
- [x] 4 outils ReAct (anomalies, STL, multi-horizon, vélocité)
- [x] Fix bug statistique AVG vs SUM
- [x] Séparation transactions vs transactions_rt

## Sprint 11 — Kanban achats + fondation data + temps réel (cette session)
- [x] Board Kanban achats (/purchase-board)
- [x] Backend supply_routes.py + supply_repo.py
- [x] Fix corruption stock (trigger trg_sync_stock_on_reco)
- [x] Durcissement agents inventaire (contexte, analyse) — fix N+1, verrous cache
- [x] 8 index PostgreSQL + 8 clés étrangères manquantes
- [x] Dédoublonnage recommandations pending + contraintes CHECK
- [x] Fix bug critique record_sale (temps réel vente→stock)
- [x] Ticker Ventes en direct + badge LIVE honnête
- [x] docs/DATA_ARCHITECTURE.md

## Backlog restant — architecture temps réel
- [ ] Retirer websocket_endpoint.py si mort (confirmer d'abord qu'aucun processus ne le lance)
- [ ] Abonné réel sur bus Redis alertes (AsyncAlertListener jamais instancié)
- [ ] Chemin rapide Redis Coach (publish_inventory_snapshot jamais appelé, fallback DB silencieux)
- [ ] Recalcul ciblé par SKU (aujourd'hui toujours tout le store, jamais une seule ligne)
- [ ] CronTrigger 15min : docstring promet un déclenchement sur alerte stock, jamais implémenté
- [ ] SupervisorAgent 2min : ne force pas de recalcul, retombe sur cache existant
- [ ] Board Kanban alertes/recommandations (précondition dédoublonnage déjà faite)
- [ ] Coach Chat : abonnement live à l'inventaire au lieu du transfert ponctuel sessionStorage
- [ ] Frontend : plusieurs types de messages WS reçus mais ignorés (processing, coach_recommendation)

## Backlog restant — base de données / tables
- [ ] Aucun lien causal alerte/recommandation → vente déclenchante (agent_run_id 100% NULL sur alerts)
- [ ] supply.stock_movements : reference_id/reference_type jamais remplis pour réception de commande (que des ventes)
- [ ] Logique "réception PO → incrément stock" jamais construite (hook identifié : supply_repo.update_status, transition RECU)
- [ ] sales.transactions_rt.cod_prod : nom incohérent avec sku ailleurs, toujours sans FK
- [ ] sales.transactions_rt.des_produit : contient parfois le SKU brut au lieu du vrai nom produit (bug simulateur)
- [ ] Tables temps réel (alerts, recommendations, transactions_rt, purchase_orders) : peuplées pour un seul store (I63), comportement multi-store jamais testé
- [ ] Schéma `agent` (8 tables) confirmé mort — à supprimer
- [ ] Pas de table catalogue fournisseur↔produit (relation seulement implicite via les PO existantes)
- [ ] customer.churn_signals vide — fonctionnalité jamais implémentée
- [ ] Tables coaching.* quasi toutes vides (coaching_recommendations, escalations, hitl_requests) sauf coaching_events

## Backlog restant — agents à améliorer (points faibles identifiés)
- [ ] Agent Stratège : ne lit jamais les signaux stock/inventaire — totalement cloisonné du contexte stock
- [ ] Agent Coach : scoring cross-domaine correct mais tourne systématiquement sur le chemin lent DB (Redis jamais alimenté)
- [ ] AnalysisAgent inventaire : variante batch de get_seasonal_demand_profile jamais câblée dans le pipeline (l'optimisation N+1 de cette session reste au niveau d'un seul SKU, pas encore du batch complet orchestrateur)
- [ ] DecisionAgent inventaire : le dédoublonnage empêche les doublons en DB mais ne corrige pas la cause racine (l'agent continue de re-générer des recommandations déjà actives)
- [ ] Aucun agent ne recalcule le "gap" (écart objectif) en réaction à une vente individuelle — seulement sur cycle ~2min
- [ ] agent_run_id incomplet : threading correct sur recommendations, jamais sur alerts

## Backlog restant — dette technique / code mort
- [ ] Consolider les 17-21 implémentations d'accès DB dupliquées (pooling incohérent, certaines sans pool du tout)
- [ ] Unifier les 5 conventions de variables d'environnement DB (POSTGRES_*, DB_*, DOCKER_DB_*, PG*, hardcodé)
- [ ] Fusionner les deux agent_logger.py divergents (root + sales-module)
- [ ] Retirer sales-module/data/repositories/postgres_repo.py (cible la base fantôme asc_db)
- [ ] Retirer monitoring-module/ (mock random, jamais branché dans main.py)
- [ ] Retirer les deux configs Alembic mortes (inventory-module + sales-module, ciblent asc_db inexistante)
- [ ] Réconcilier la logique dupliquée json_service.py vs postgres_provider.py (même calcul CA, deux implémentations différentes)
- [ ] Supprimer les modèles Pydantic jamais importés (sales-module/core/models.py, monitoring-module/schemas.py)
- [ ] Décider d'une source de vérité unique pour les migrations (SQL + runner actuel vs Alembic re-baselineé)

## Backlog restant — sécurité / robustesse
- [ ] RBAC store-level existe seulement sur /chat — pas sur les endpoints inventory/supply (Kanban achats inclus)
- [ ] Pas de rate limiting sur les endpoints inventory/supply (seulement /chat et /stream)
- [ ] .env commité historiquement avec clés API réelles (signalé dans l'audit architecture initial — vérifier rotation)
