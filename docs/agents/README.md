# Agents du système — index

Ce dossier décrit, un fichier par agent, le rôle et le fonctionnement de chacun des composants intelligents du système Ooredoo Tunisia Sales & Inventory. Chaque fichier est écrit en texte descriptif, sans extrait de code, et se comprend indépendamment des autres.

- [SupervisorAgent — l'orchestrateur central](00-supervisor-agent.md) — démarre et coordonne un cycle complet, fusionne les branches, applique le verdict du Guardrail.
- [Agent Analyste (APP02)](01-agent-analyste.md) — diagnostic temps réel de la performance du magasin, en boucle Observation-Raisonnement-Action.
- [Agent Stratège (APP05)](02-agent-stratege.md) — transforme le diagnostic en plan d'action, produits à pousser et message au manager.
- [Agent Coach (APP07)](03-agent-coach.md) — recommandation de produit scorée et CoachAgent conversationnel du chat conseiller.
- [Agent RAG — Connaissance partagée (APP10)](04-agent-rag.md) — retrouve les scripts de vente historiques les plus pertinents pour une situation donnée.
- [Agent Guardrail (APP08)](05-agent-guardrail.md) — valide chaque recommandation contre sept règles métier avant diffusion.
- [Inventory Analysis Agent (INV-A)](06-inventory-agent-analyse.md) — diagnostic de risque de stock et métriques de réapprovisionnement par référence.
- [Inventory Context Agent (INV-C)](07-inventory-agent-contexte.md) — signaux externes (météo, promos, événements) ajustant la demande prévue.
- [Inventory Decision Agent (INV-D)](08-inventory-agent-decision.md) — décision finale de réapprovisionnement, explicable et actionnable.
- [HITL — Validation humaine](09-hitl.md) — point de contrôle manager pour les décisions escaladées par le Guardrail.
- [Page Monitoring — ce qu'elle observe](10-page-monitoring.md) — description détaillée de chaque panneau de la page de supervision technique.

Pour une vue d'ensemble de l'architecture globale (état partagé RetailState, topologie du graphe, flux de données bout-en-bout), voir [ARCHITECTURE_AGENTS.md](../ARCHITECTURE_AGENTS.md) à la racine de `docs/`.
