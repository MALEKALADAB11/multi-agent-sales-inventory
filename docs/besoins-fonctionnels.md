# Spécification des besoins — Système multi-agents de coaching commercial et d'optimisation des stocks

## 1. Besoins fonctionnels

### 1.1 Analyse et suivi de l'état des stocks

- **BF-1.1 — Consultation de l'état des stocks :** l'utilisateur doit pouvoir consulter la
  disponibilité actuelle des produits dans chaque point de vente, avec les informations nécessaires
  pour évaluer la situation du stock.
- **BF-1.2 — Identification des risques de stock :** le système doit permettre à l'utilisateur
  d'identifier rapidement les produits présentant des risques (rupture, stock insuffisant ou
  excédent de stock) grâce à des indicateurs visuels.
- **BF-1.3 — Prise en compte du contexte de demande :** l'utilisateur doit pouvoir consulter
  les éléments pouvant influencer la demande future, tels que les promotions, événements ou
  périodes particulières.
- **BF-1.4 — Consultation des prévisions de demande :** l'utilisateur doit pouvoir visualiser
  les prévisions de demande des produits afin d'anticiper les besoins futurs en stock.

### 1.2 Assistance à la décision de réapprovisionnement

- **BF-2.1 — Proposition d'actions de réapprovisionnement :** le système doit fournir des
  recommandations adaptées pour chaque produit afin d'aider l'utilisateur à décider s'il faut
  commander, accélérer un approvisionnement, maintenir la situation actuelle ou effectuer un suivi.
- **BF-2.2 — Suggestion des quantités à commander :** le système doit proposer une quantité de
  réapprovisionnement recommandée pour chaque produit afin d'aider l'utilisateur à préparer
  ses commandes.
- **BF-2.3 — Adaptation aux objectifs du magasin :** les recommandations doivent pouvoir prendre
  en compte les priorités du magasin, comme la disponibilité des produits ou la réduction des
  coûts liés au stockage.

### 1.3 Validation et gestion des commandes

- **BF-3.1 — Explication des recommandations :** le système doit fournir une explication
  compréhensible des recommandations proposées afin que l'utilisateur puisse comprendre les
  raisons derrière chaque décision.
- **BF-3.2 — Validation humaine des décisions :** l'utilisateur doit pouvoir accepter, modifier
  ou refuser les recommandations générées avant leur application.
- **BF-3.3 — Suivi du cycle des commandes :** l'utilisateur doit pouvoir suivre l'évolution des
  commandes d'approvisionnement et gérer leurs différents états jusqu'à leur réception.

### 1.4 Coaching commercial en temps réel

- **BF-4.1 — Assistant conversationnel de coaching :** le conseiller de vente doit pouvoir
  interagir en langage naturel avec un assistant qui répond à ses questions sur les produits,
  les ventes et les actions à mener dans son point de vente.
- **BF-4.2 — Recommandations de produits à proposer :** le système doit suggérer au conseiller
  les produits les plus pertinents à mettre en avant, classés par un score tenant compte des
  ventes, de la disponibilité en stock et du contexte du moment.
- **BF-4.3 — Scripts et arguments de vente :** le système doit fournir des scripts et arguments
  de vente adaptés à la situation (produit, moment de la journée, profil client), issus d'une
  base de connaissances métier.
- **BF-4.4 — Réponse progressive en temps réel :** les réponses de l'assistant doivent s'afficher
  au fur et à mesure de leur génération afin que le conseiller puisse les exploiter sans délai
  pendant l'interaction avec le client.

### 1.5 Analyse des performances de vente

- **BF-5.1 — Consultation des indicateurs de vente :** l'utilisateur doit pouvoir consulter les
  indicateurs clés de son point de vente (volumes, tendances, comparaisons périodiques) sous
  forme de tableaux de bord synthétiques.
- **BF-5.2 — Prévisions de ventes :** le système doit produire des prévisions de ventes
  multi-horizons afin d'anticiper l'activité et d'alimenter les décisions commerciales et de stock.
- **BF-5.3 — Détection d'anomalies et de tendances :** le système doit détecter automatiquement
  les comportements de vente atypiques (chute, pic, rupture de tendance, saisonnalité) et les
  signaler à l'utilisateur.
- **BF-5.4 — Prise en compte du profil du conseiller :** le coaching doit pouvoir s'adapter à
  l'historique et au profil du conseiller afin de personnaliser les recommandations.

### 1.6 Contexte marché et stratégie commerciale

- **BF-6.1 — Intégration du contexte marché :** le système doit intégrer les événements datés
  (festivals, périodes particulières) et les offres commerciales actives dans ses analyses et
  recommandations, en distinguant les événements en cours et à venir.
- **BF-6.2 — Recommandations stratégiques cross-domaine :** le système doit produire des
  recommandations stratégiques combinant les signaux de vente et l'état des stocks, afin d'éviter
  par exemple de promouvoir un produit en risque de rupture.
- **BF-6.3 — Alertes proactives :** le système doit notifier l'utilisateur en temps réel des
  situations nécessitant une action (commerciale ou de stock), sans que celui-ci ait à les
  rechercher.

### 1.7 Contrôle et fiabilité des réponses générées

- **BF-7.1 — Contrôle automatique des réponses :** chaque réponse générée doit être vérifiée
  par un agent de contrôle (Guardrail) avant affichage : conformité métier, absence de contenus
  sensibles ou erronés, avec indication visible du statut de vérification.
- **BF-7.2 — Validation humaine des cas sensibles :** lorsque la réponse ou la décision est
  jugée sensible, le système doit la soumettre à une validation humaine avant application
  (Human-in-the-Loop).
- **BF-7.3 — Réponse de repli :** en cas de réponse jugée non conforme, le système doit fournir
  une réponse de repli sûre plutôt qu'une absence de réponse ou un contenu risqué.

### 1.8 Orchestration, sécurité et communication

- **BF-8.1 — Orchestration multi-agents :** le système doit coordonner l'exécution parallèle des
  agents spécialisés (analyse, connaissance, contexte, stock) et fusionner leurs résultats en une
  réponse unique et cohérente.
- **BF-8.2 — Authentification et contrôle d'accès :** l'accès aux fonctionnalités et aux données
  doit être restreint selon le rôle de l'utilisateur et son point de vente.
- **BF-8.3 — Boucle de feedback :** les décisions humaines (acceptation, modification, refus des
  recommandations) doivent être enregistrées et exploitables pour améliorer les recommandations
  futures.
- **BF-8.4 — Communication inter-domaines :** les analyses commerciales doivent pouvoir exploiter
  l'état des stocks et réciproquement, via des outils partagés entre les agents.
- **BF-8.5 — Notifications temps réel :** le système doit diffuser en temps réel les événements
  importants (alertes stock, statut de vérification, mouvements de commandes) vers l'interface
  utilisateur.

---

## 2. Besoins non fonctionnels

### 2.1 Performance et réactivité

- **BNF-1.1 — Temps de traitement des analyses :** le système doit être capable d'effectuer
  l'analyse des stocks et de générer les recommandations dans un délai compatible avec une
  utilisation opérationnelle.
- **BNF-1.2 — Réactivité de l'application :** les actions effectuées par l'utilisateur sur
  l'interface doivent être prises en compte rapidement afin d'assurer une expérience
  utilisateur fluide.
- **BNF-1.3 — Latence du coaching conversationnel :** le premier élément de réponse de l'assistant
  doit apparaître dans un délai de quelques secondes, le raisonnement multi-agents étant exécuté
  en parallèle et le premier token diffusé en streaming.

### 2.2 Disponibilité et robustesse

- **BNF-2.1 — Continuité des fonctionnalités principales :** le système doit maintenir les
  fonctionnalités essentielles d'analyse et de recommandation même en cas d'indisponibilité
  temporaire de certains composants externes.
- **BNF-2.2 — Gestion des erreurs :** le système doit détecter les erreurs lors des traitements
  ou des interactions utilisateur et fournir un retour approprié afin d'éviter des comportements
  inattendus.
- **BNF-2.3 — Résilience aux fournisseurs de modèles :** en cas d'indisponibilité du fournisseur
  de modèle de langage principal, le système doit basculer automatiquement vers un fournisseur
  alternatif sans interruption du service.
- **BNF-2.4 — Dégradation contrôlée :** l'indisponibilité d'un composant (base vectorielle,
  service de prévision, cache) doit déclencher un mécanisme de repli — corpus local pour la
  recherche documentaire, méthode statistique pour les prévisions — sans bloquer le flux principal.

### 2.3 Sécurité et intégrité des données

- **BNF-3.1 — Gestion des droits d'accès :** le système doit contrôler l'accès aux données et
  aux fonctionnalités selon les rôles et les permissions des utilisateurs.
- **BNF-3.2 — Cohérence des opérations métier :** le système doit garantir que les actions
  réalisées sur les stocks et les commandes respectent les règles métier définies.
- **BNF-3.3 — Limitation de charge :** les points d'accès exposés doivent être protégés par une
  limitation de débit afin de préserver la stabilité du service et de maîtriser la consommation
  des API de modèles.

### 2.4 Utilisabilité et expérience utilisateur

- **BNF-4.1 — Compréhension des informations affichées :** l'utilisateur doit pouvoir comprendre
  rapidement l'état des stocks, les risques détectés et les recommandations proposées.
- **BNF-4.2 — Retour utilisateur :** les actions effectuées dans l'application doivent fournir
  un retour clair indiquant leur résultat.
- **BNF-4.3 — Visualisation des informations critiques :** le système doit permettre d'identifier
  facilement les situations nécessitant une attention particulière, comme les risques de rupture
  ou les besoins de réapprovisionnement.

### 2.5 Traçabilité et explicabilité

- **BNF-5.1 — Historique des recommandations :** le système doit conserver les informations
  nécessaires au suivi des recommandations générées et des décisions prises par les utilisateurs.
- **BNF-5.2 — Explication des décisions automatisées :** les recommandations générées
  automatiquement doivent être accompagnées d'éléments permettant à l'utilisateur de comprendre
  les raisons de la décision proposée.
- **BNF-5.3 — Suivi du fonctionnement du système :** le système doit permettre le suivi des
  traitements réalisés afin de faciliter l'analyse des performances et l'amélioration du système.
- **BNF-5.4 — Observabilité des agents :** chaque exécution du graphe multi-agents doit être
  tracée (entrées, sorties, durées, coûts) afin de permettre l'analyse des performances et
  l'évaluation continue de la qualité des réponses.

---

## 3. Matrice de traçabilité besoins ↔ composants

| Besoin | Composant(s) réalisant le besoin |
|---|---|
| BF-1.1 / BF-1.2 | Agent Analysis, dashboard stocks, indicateurs visuels de risque |
| BF-1.3 | Agent Context (événements, promotions, saisonnalité) |
| BF-1.4 | Moteur de prévision (Holt-Winters, TimesFM, fallback SQL) |
| BF-2.1 / BF-2.2 / BF-2.3 | Agent Decision (action + quantité + objectifs magasin) |
| BF-3.1 | Justifications générées par l'Agent Decision |
| BF-3.2 | Porte HITL, panneau de validation Angular |
| BF-3.3 | Kanban commandes (SUGGÉRÉ → EN ATTENTE → LIVRAISON → REÇU) |
| BF-4.1 / BF-4.4 | Agent Coach, endpoints `/chat` et `/stream` (SSE) |
| BF-4.2 | Scoring cross-domaine des produits |
| BF-4.3 | RAG Milvus + corpus de scripts de vente |
| BF-5.1 à BF-5.3 | Agent Analyste (moteur séries temporelles, anomalies, prévisions) |
| BF-5.4 | Profil conseiller |
| BF-6.1 / BF-6.2 | Agent Stratège (événements, offres, actions cross-domaine) |
| BF-6.3 | AlertBus + WebSocket temps réel |
| BF-7.1 / BF-7.3 | Agent Guardrail + nœud safe_fallback |
| BF-7.2 | Nœud human_validation (HITL) |
| BF-8.1 | SupervisorAgent (LangGraph, branches parallèles, RetailState) |
| BF-8.2 | Auth JWT + RBAC store-level |
| BF-8.3 | Boucle de feedback humain |
| BF-8.4 | Outils cross-domaine + serveur MCP |
| BF-8.5 | Feeds WebSocket (alertes, guardrail, Kanban) |
