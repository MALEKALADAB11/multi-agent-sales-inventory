# Besoins Fonctionnels — Module Inventory (Vision Utilisateur)

> **Principe** : Ce document décrit ce que les utilisateurs peuvent **faire** avec le système, pas comment il fonctionne techniquement.

---

## 5.3 Besoins Fonctionnels — Vision Utilisateur

### Phase 1 : Accès et Compréhension de la Situation Stock

**BF-1.1 : Consulter l'état des stocks en temps réel**
- L'utilisateur peut visualiser, pour chaque produit de son magasin, le niveau de stock actuel, le nombre de jours de stock restants, et le statut de risque (CRITICAL/HIGH/MEDIUM/LOW)
- L'utilisateur peut filtrer et trier les produits par niveau de risque, catégorie, ou métrique de stock

**BF-1.2 : Accéder aux prévisions de demande**
- L'utilisateur peut consulter les prévisions de ventes à 7 jours pour chaque produit, basées sur l'historique et les modèles de séries temporelles
- L'utilisateur peut voir l'intervalle de confiance associé à chaque prévision (incertitude)

**BF-1.3 : Comprendre le contexte impactant la demande**
- L'utilisateur peut voir les facteurs contextuels actuels qui influencent la demande de chaque produit : promotions en cours, météo, jours fériés, événements marché
- L'utilisateur peut comprendre quel signal est dominant (ex: "promotion +30%") et avec quel niveau de confiance

**BF-1.4 : Visualiser les métriques de réapprovisionnement**
- L'utilisateur peut accéder aux métriques calculées : point de commande, stock de sécurité, quantité économique de commande (EOQ), coût total de réapprovisionnement
- L'utilisateur peut voir comment ces métriques s'ajustent en fonction du contexte (demand uplift)

---

### Phase 2 : Prise de Décision Assistée

**BF-2.1 : Recevoir des recommandations d'action**
- L'utilisateur reçoit, pour chaque produit à risque, une recommandation d'action claire : ORDER (commander), EXPEDITE (expédier en urgence), HOLD (surveiller), ou MONITOR (ne rien faire)
- Chaque recommandation inclut la quantité suggérée, le niveau d'urgence (immédiat/cette semaine/ce mois), et une justification en langage naturel

**BF-2.2 : Comprendre le raisonnement derrière chaque recommandation**
- L'utilisateur peut lire, pour chaque recommandation, l'explication complète : contrainte physique ou financière justificative, influence du signal contextuel, alertes particulières
- L'utilisateur peut voir le compromis assumé (coût ou risque accepté en suivant la recommandation)

**BF-2.3 : Évaluer les arbitrages et scénarios alternatifs**
- L'utilisateur peut voir, pour les situations complexes, des scénarios alternatifs avec leurs implications (ex: commander moins mais accepter un risque de rupture plus élevé)
- L'utilisateur peut comprendre l'impact de chaque option sur les objectifs business actuels du magasin

**BF-2.4 : Valider ou modifier les recommandations**
- L'utilisateur peut accepter une recommandation telle quelle
- L'utilisateur peut ajuster manuellement la quantité proposée avec justification
- L'utilisateur peut rejeter une recommandation et enregistrer le motif

---

### Phase 3 : Gestion des Commandes Fournisseurs

**BF-3.1 : Créer et gérer les bons de commande**
- L'utilisateur peut générer des bons de commande (PO) à partir des recommandations acceptées
- L'utilisateur peut regrouper plusieurs recommandations en un seul PO pour un fournisseur
- L'utilisateur peut spécifier ou modifier les dates de livraison attendues

**BF-3.2 : Suivre l'état des commandes en cours**
- L'utilisateur peut visualiser un tableau Kanban des commandes : EN ATTENTE → VALIDÉ → COMMANDÉ → EN TRANSIT → REÇU
- L'utilisateur peut voir, pour chaque commande, les produits inclus, les quantités, le fournisseur, et l'état actuel

**BF-3.3 : Réceptionner et intégrer les livraisons**
- L'utilisateur peut enregistrer la réception d'une commande
- L'utilisateur peut signaler les écarts de quantité reçue vs commandée
- Le système met automatiquement à jour le stock lors de la réception

**BF-3.4 : Gérer les relations fournisseurs**
- L'utilisateur peut voir, pour chaque fournisseur, l'historique des commandes, les délais moyens, et la fiabilité
- L'utilisateur peut accéder au catalogue fournisseur pour les produits disponibles

---

### Phase 4 : Supervision et Contrôle

**BF-4.1 : Superviser la santé du système**
- L'utilisateur peut accéder à un dashboard montrant l'état de santé des agents (latence, taux de succès, erreurs)
- L'utilisateur peut voir les coûts d'exploitation estimés (appels LLM, temps de calcul)

**BF-4.2 : Valider les décisions à fort enjeu (HITL)**
- L'utilisateur (manager) peut consulter la liste des décisions nécessitant une validation humaine (coût élevé ou confiance faible)
- L'utilisateur peut approuver ou rejeter ces décisions avec traçabilité complète

**BF-4.3 : Consulter l'historique et les KPIs**
- L'utilisateur peut accéder à l'historique des recommandations et décisions passées
- L'utilisateur peut voir les KPIs de performance : taux de rupture, taux de surstock, respect des objectifs business, coût total des stocks

**BF-4.4 : Configurer les objectifs business**
- L'utilisateur peut définir ou modifier l'objectif prioritaire du magasin : réduction des coûts, niveau de service client, ou équilibre
- L'utilisateur peut ajuster les seuils de service level target

**BF-4.5 : Gérer les alertes en temps réel**
- L'utilisateur peut recevoir des alertes temps réel sur les situations critiques (rupture imminente, surstock détecté)
- L'utilisateur peut voir ces alertes sur un dashboard et les traiter

---

### Phase 5 : Évaluation et Amélioration Continue

**BF-5.1 : Évaluer la qualité des recommandations**
- L'utilisateur peut noter la pertinence des recommandations reçues (après exécution)
- L'utilisateur peut signaler les recommandations inappropriées ou incorrectes

**BF-5.2 : Consulter les métriques de performance du système**
- L'utilisateur peut voir des métriques agrégées : précision des prévisions, taux de recommandations suivies, impact sur les ruptures de stock
- L'utilisateur peut comparer la performance avant vs après déploiement du système

**BF-5.3 : Accéder aux rapports d'audit**
- L'utilisateur peut consulter la traçabilité complète : quelle recommandation, quel agent, quelles données d'entrée, quelle décision finale
- L'utilisateur peut exporter ces rapports pour analyse ou conformité

---

## Notes sur la restructuration

### Ce qui a changé par rapport à la version initiale :

1. **Phase 3 (ex-Design Agentique)** → Remplacée par **Phase 3 : Gestion des Commandes Fournisseurs**
   - L'ancienne version décrivait l'architecture interne (workflow multi-agents, évaluation des risques)
   - La nouvelle version décrit ce que l'utilisateur fait : créer des PO, suivre leur état, réceptionner les livraisons

2. **Phase 1 (ex-Data Engineering)** → Renommée en **Phase 1 : Accès et Compréhension**
   - L'ancienne version parlait d'ingestion de données (technique)
   - La nouvelle version parle de ce que l'utilisateur peut consulter et comprendre

3. **Phase 2 (ex-Modélisation Prédictive)** → Renommée en **Phase 2 : Prise de Décision Assistée**
   - L'ancienne version décrivait les modèles de prévision (technique)
   - La nouvelle version décrit comment l'utilisateur utilise ces prévisions pour décider

4. **Phase 4 (ex-Génération de Recommandations)** → Partiellement intégrée dans Phase 2
   - Les recommandations sont maintenant au cœur de la Phase 2 (décision)
   - Phase 4 devient "Supervision et Contrôle" (monitoring, HITL, KPIs)

5. **Nouvelle Phase 5 : Évaluation et Amélioration Continue**
   - Ajoutée pour couvrir l'évaluation de la qualité des recommandations par les utilisateurs
   - Couvre les métriques de performance et l'auditabilité

### Ce qui a été ajouté :

- **Gestion des commandes fournisseurs** (Kanban PO) - absent de la version initiale
- **Validation humaine (HITL)** - décrit comme capacité utilisateur
- **Configuration des objectifs business** - l'utilisateur peut piloter les priorités
- **Alertes temps réel** - capacité utilisateur à réagir aux situations critiques
- **Évaluation de la qualité des recommandations** - feedback utilisateur pour amélioration continue

### Ce qui a été supprimé (car trop technique) :

- "Ingestion des données" → remplacé par "Consulter l'état des stocks"
- "Intégration des prévisions de ventes" → remplacé par "Accéder aux prévisions de demande"
- "Workflow multi-agents" → supprimé (détail d'implémentation)
- "Détection des variations de tendance" → intégré dans "Comprendre le contexte"
- "Mesure de l'incertitude" → intégré dans "Accéder aux prévisions"
