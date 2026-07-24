# Diagrammes UML — Cas d'utilisation & diagrammes de séquence

Ce dossier contient l'ensemble des diagrammes UML du projet pour le rapport :

- **[use-cases.puml](use-cases.puml)** — 3 diagrammes de cas d'utilisation en **PlantUML** :
  1. `use_cases_global` — vue globale (4 acteurs, 6 packages, 17 cas d'utilisation)
  2. `use_cases_coaching` — détail du coaching conversationnel (include/extend Guardrail, HITL, repli)
  3. `use_cases_reappro` — détail du réapprovisionnement (déclenchement humain ou par alerte, cycle commande)
- **[sequences/](sequences/)** — 15 diagrammes de séquence en **Mermaid**, un fichier par scénario.

**Rendu :**
- PlantUML : extension *PlantUML* de VS Code (`Alt+D`), ou https://www.plantuml.com/plantuml
- Mermaid : aperçu Markdown de VS Code, GitHub, ou https://mermaid.live (export PNG/SVG pour le rapport)

---

## Index des diagrammes de séquence

| Fichier | Scénario | User stories | Besoins |
|---|---|---|---|
| [seq-01](sequences/seq-01-authentification.md) | Authentification, JWT, RBAC store-level, refresh token | US-8.1 | BF-8.2, BNF-3.1 |
| [seq-02](sequences/seq-02-consultation-stocks.md) | Consultation des stocks + indicateurs de risque (cache Redis) | US-2.1, US-2.2 | BF-1.1, BF-1.2 |
| [seq-03](sequences/seq-03-dashboard-ventes.md) | Dashboard ventes, KPIs, tendances, gestion d'erreur | US-2.3, US-2.4, US-2.6 | BF-5.1, BNF-4.x |
| [seq-04](sequences/seq-04-previsions-demande.md) | Prévisions de demande (TimesFM → Holt-Winters → SQL) | US-3.1, US-3.2, US-3.3 | BF-1.4, BF-5.2, BNF-2.4 |
| [seq-05](sequences/seq-05-coach-chat-streaming.md) | Chat coach en streaming SSE, RAG avec repli, fallback LLM | US-4.1 à 4.3, US-4.5, US-4.6, US-7.5 | BF-4.x, BNF-1.3, BNF-2.3 |
| [seq-06](sequences/seq-06-orchestration-supervisor.md) | Orchestration LangGraph : 4 branches parallèles, fusion, routage | US-7.1 à 7.4 | BF-8.1, BNF-5.4 |
| [seq-07](sequences/seq-07-guardrail-controle.md) | Guardrail : conforme / sensible (HITL) / bloqué (repli sûr) | US-8.3, US-8.4, US-8.5 | BF-7.1 à 7.3 |
| [seq-08](sequences/seq-08-analyse-stocks-agents.md) | Agents Analysis + Context : diagnostic stock et demande | US-5.1, US-5.2 | BF-1.x, BNF-1.1 |
| [seq-09](sequences/seq-09-recommandation-reappro.md) | Agent Decision : action, quantité, contraintes, explication | US-5.3 à 5.6 | BF-2.x, BF-3.1 |
| [seq-10](sequences/seq-10-validation-hitl-commande.md) | Validation humaine (approuver / modifier / refuser) + feedback | US-8.2, US-9.2, US-10.4 | BF-3.2, BF-8.3 |
| [seq-11](sequences/seq-11-kanban-cycle-commande.md) | Kanban : cycle SUGGÉRÉ → REÇU, maj stock, temps réel partagé | US-9.1, US-9.5, US-9.6 | BF-3.3, BNF-3.2 |
| [seq-12](sequences/seq-12-alertes-temps-reel.md) | AlertBus : vente → seuil → cycle proactif → notification WS | US-9.3, US-9.4 | BF-6.3, BF-8.5 |
| [seq-13](sequences/seq-13-analyste-anomalies.md) | Agent Analyste ReAct : anomalies, décomposition, vélocité | US-3.4, US-3.5, US-3.6 | BF-5.3 |
| [seq-14](sequences/seq-14-stratege-cross-domaine.md) | Agent Stratège + scoring cross-domaine des produits | US-4.4, US-6.1 à 6.3 | BF-4.2, BF-6.x |
| [seq-15](sequences/seq-15-observabilite-evaluation.md) | Traçage Langfuse + suite d'évaluation avec LLM juge | US-7.4, US-10.5 | BNF-5.3, BNF-5.4 |

## Couverture des user stories

Toutes les user stories **fonctionnelles** du backlog sont couvertes par au moins un diagramme
de séquence (voir la colonne « User stories » ci-dessus).

Les user stories **techniques** sans interaction utilisateur observable ne donnent pas lieu à un
diagramme de séquence (elles relèvent du diagramme de déploiement / de composants) :

| User story | Nature | Où elle apparaît |
|---|---|---|
| US-1.1 à US-1.5 | Socle (données, migrations, squelettes) | Architecture en couches (`architecture-multi-agents.md` §1) |
| US-2.5 | Responsive mobile (CSS) | — non séquentiel |
| US-5.7 | Serveur MCP (exposition d'outils) | Architecture §1 (couche services) |
| US-8.6 | Rate limiting (middleware) | Mentionné en garde d'entrée API |
| US-10.1 à 10.3 | Tests et CI/CD | Chapitre qualité du rapport |
| US-10.6 | Consolidation architecture | Chapitre architecture du rapport |
