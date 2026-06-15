"""
SETUP CHECKLIST — Coach Chat ↔ Stratège Robuste

⚠️ ÉTAPES À EFFECTUER POUR ACTIVER LE SYSTÈME
"""

# ══════════════════════════════════════════════════════════════════════════════
# FICHIERS DÉJÀ CRÉÉS ✅
# ══════════════════════════════════════════════════════════════════════════════

"""
✅ sales-module/modules/coaching/orchestrator/
   ├── __init__.py                          (créé)
   ├── coach_stratege_orchestrator.py       (créé - 450 lignes)
   ├── coach_stratege_interface.py          (créé - 100 lignes)
   ├── bootstrap.py                         (créé - initialisation)
   └── COACH_STRATEGE_INTEGRATION.md        (créé - documentation)

✅ sales-module/modules/coaching/agents/coach/
   ├── agent.py                             (modifié - graphe v2)
   ├── node_invoke_stratege.py              (créé - nouveau node)
   └── nodes.py                             (inchangé)

✅ sales-module/tests/
   └── test_coach_stratege_integration.py   (créé - tests complets)
"""

# ══════════════════════════════════════════════════════════════════════════════
# ACTIONS À FAIRE (3 ÉTAPES SIMPLES)
# ══════════════════════════════════════════════════════════════════════════════

# ÉTAPE 1 — Modifier main.py ou sales-module/main.py
# ─────────────────────────────────────────────────────────────────────────────
# 
# Ajouter dans lifespan :
#
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     logger.info("Starting AI Sales Coach backend...")
#     
#     # ⬇️ AJOUTER CES LIGNES ⬇️
#     from modules.coaching.orchestrator.bootstrap import initialize_coach_stratege_orchestrator
#     try:
#         await initialize_coach_stratege_orchestrator()
#         logger.info("✅ Coach-Stratège orchestrator initialized")
#     except Exception as e:
#         logger.error(f"❌ Failed to init orchestrator: {e}")
#         raise
#     # ⬆️ FIN AJOUT ⬆️
#     
#     # ... reste du code ...
#     
#     yield
#     
#     # cleanup
#

# ÉTAPE 2 — Vérifier que les imports fonctionnent
# ─────────────────────────────────────────────────────────────────────────────
#
# cd sales-module
# python -c "from modules.coaching.orchestrator import get_orchestrator; print('✓ Import OK')"
#
# Si erreur : vérifier que le dossier orchestrator/ a __init__.py
#

# ÉTAPE 3 — Tester le flux complet (optionnel)
# ─────────────────────────────────────────────────────────────────────────────
#
# cd sales-module
# pytest tests/test_coach_stratege_integration.py -v
#
# ou
#
# python -m pytest tests/test_coach_stratege_integration.py::test_full_flow_coach_to_stratege -v -s
#

# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION POST-ACTIVATION
# ══════════════════════════════════════════════════════════════════════════════

"""
Après activation, vous devez voir dans les logs au démarrage :

[BOOTSTRAP] Initialisation orchestrateur Coach-Stratège...
[BOOTSTRAP] Compilation agent Stratège...
[BOOTSTRAP] ✓ Agent Stratège compilé
[BOOTSTRAP] Création orchestrateur...
[BOOTSTRAP] ✓ Orchestrateur initialisé et accessible
[BOOTSTRAP] Cache stats: {'hits': 0, 'misses': 0, 'hit_rate': 0.0, 'size': 0}
[BOOTSTRAP] ✓ Orchestrateur Coach-Stratège prêt

Si vous ne voyez pas ces logs → l'initialisation n'a pas été appelée
"""

# ══════════════════════════════════════════════════════════════════════════════
# ARCHITECTURE RÉSUMÉE
# ══════════════════════════════════════════════════════════════════════════════

"""
Coach Agent (6 nodes):
  1. load_context
  2. rag_search
  3. load_advisor_history
  4. invoke_stratege_for_coach ← NOUVEAU (appelle orchestrateur)
  5. generate_conseil
  6. save_conseil

L'orchestrateur gère :
  ✓ Cache (LRU 30 min)
  ✓ Retry (3 tentatives)
  ✓ Timeout (30s)
  ✓ Fallbacks (4 niveaux)
  ✓ Logging détaillé

Résultat:
  ✓ Coach JAMAIS cassé (même si Stratège down)
  ✓ Performance optimale (cache <1ms)
  ✓ Production-ready
"""

# ══════════════════════════════════════════════════════════════════════════════
# FICHIERS DE RÉFÉRENCE
# ══════════════════════════════════════════════════════════════════════════════

"""
Documentation:
  📖 sales-module/modules/coaching/orchestrator/COACH_STRATEGE_INTEGRATION.md

Code principal:
  🔧 coach_stratege_orchestrator.py (450L)
     - CoachStrategieOrchestrator (classe principale)
     - StrategyCache (LRU cache)
     - StrategieOutput (dataclass)

  🔌 coach_stratege_interface.py (100L)
     - CoachStrategieInterface (wrapper haut niveau)

  🚀 bootstrap.py (50L)
     - initialize_coach_stratege_orchestrator()

  📦 __init__.py
     - Exports publiques

Node Coach:
  🔗 node_invoke_stratege.py
     - node_invoke_stratege_for_coach()

Tests:
  🧪 tests/test_coach_stratege_integration.py
     - 15+ tests
     - Coverage complet
"""

# ══════════════════════════════════════════════════════════════════════════════
# TROUBLESHOOTING
# ══════════════════════════════════════════════════════════════════════════════

"""
❌ Erreur: "ModuleNotFoundError: No module named 'modules.coaching.orchestrator'"
   → Vérifier que orchestrator/ a __init__.py (déjà créé ✓)
   → Vérifier PYTHONPATH inclut sales-module

❌ Erreur: "Orchestrator not initialized"
   → initialize_coach_stratege_orchestrator() pas appelé
   → Ajouter dans lifespan (voir ÉTAPE 1)

❌ Performance lente (>200ms)
   → D'abord : est-ce "fresh" ou "cached"?
   → Si fresh : Stratège lui-même lent
   → Vérifier Ollama, Milvus, réseau

❌ Cache ne fonctionne pas
   → Vérifier hit_rate avec: orchestrator.get_stats()
   → Si 0% : vérifier que même gap_pct arrondit bien

❌ Coach break / Stratège erreur
   → Fallback automatique activé ✓
   → Vérifier logs [COACH-STRATEGE] FALLBACK
   → Actions par défaut = toujours disponibles
"""

# ══════════════════════════════════════════════════════════════════════════════
# PROCHAINES ÉTAPES RECOMMANDÉES
# ══════════════════════════════════════════════════════════════════════════════

"""
Phase 1 — AUJOURD'HUI:
  ☐ Ajouter import + initialize dans main.py (5 min)
  ☐ Tester démarrage (vérifier logs) (5 min)
  ☐ Appeler /api/v1/coach/chat et vérifier strategie_source en réponse (5 min)
  
Phase 2 — DEMAIN:
  ☐ Vérifier logs détaillés du Coach : cache hit/miss ratio
  ☐ Tester fallback : arrêter Stratège volontairement
  ☐ Vérifier performances avec load test
  
Phase 3 — CETTE SEMAINE:
  ☐ Ajouter monitoring Prometheus (Cache hit rate, duration)
  ☐ Configurer alertes si Stratège timeout fréquent
  ☐ Documenter dans runbook ops
  
Phase 4 — FUTUR:
  ☐ Cache Redis pour multi-instance
  ☐ Tracing distribué (OpenTelemetry)
  ☐ Metrics temps réel dashboard
"""

# ══════════════════════════════════════════════════════════════════════════════
# CHECKLIST FINAL ACTIVATION
# ══════════════════════════════════════════════════════════════════════════════

ACTIVATION_CHECKLIST = """
☐ Fichiers créés validés :
    ☐ orchestrator/__init__.py existe
    ☐ coach_stratege_orchestrator.py existe
    ☐ coach_stratege_interface.py existe
    ☐ bootstrap.py existe
    ☐ node_invoke_stratege.py existe (dans agents/coach/)
    
☐ Modifications appliquées :
    ☐ agent.py (coach) inclut node_invoke_stratege_for_coach
    ☐ agent.py imports modifiés
    
☐ Code de démarrage modifié :
    ☐ main.py inclut import initialize_coach_stratege_orchestrator
    ☐ lifespan() appelle initialize_coach_stratege_orchestrator()
    
☐ Imports testés :
    ☐ python -c "from modules.coaching.orchestrator import get_orchestrator"
    
☐ Démarrage réussi :
    ☐ Backend démarre sans erreur
    ☐ Logs montrent "[BOOTSTRAP] ✓ Orchestrateur prêt"
    
☐ Flux testé :
    ☐ Appel /api/v1/coach/chat retourne strategie_actions
    ☐ strategie_source = "success" ou "cached"
    
✅ ACTIVATION COMPLÈTE
"""

print(ACTIVATION_CHECKLIST)
