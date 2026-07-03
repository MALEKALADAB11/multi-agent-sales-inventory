# Page Monitoring — ce qu'elle observe

## Rôle de la page

La page Monitoring est la fenêtre d'observabilité technique du système : elle ne s'adresse pas au conseiller ni même directement au manager de magasin dans son activité quotidienne, mais à quiconque doit vérifier que les huit agents décrits dans ce dossier fonctionnent réellement, à quelle vitesse, avec quel taux de succès, et à quel coût. Elle répond à une question simple mais essentielle dans un système multi-agents : est-ce que chaque agent tourne, et si l'un d'eux se comporte mal, lequel, depuis quand, et pourquoi.

Toutes les données affichées proviennent de deux sources réelles en base de données : un journal d'exécution détaillé pour les quatre agents du domaine commercial — Analyste, Stratège, Coach et Guardrail — qui enregistre chaque étape de raisonnement avec ses entrées et sorties réelles, et un journal d'exécution par lot pour les trois agents du domaine inventaire, qui enregistre chaque passage complet sur une référence produit avec son statut et son résultat chiffré. L'Agent RAG partage le même journal détaillé que les agents commerciaux, puisque chaque recherche de script qu'il effectue — que ce soit pour le Stratège ou pour le CoachAgent — y est également consignée.

## La barre d'indicateurs en haut de page

Cinq chiffres résument l'état du système en un coup d'œil. Le nombre d'agents en bonne santé compte, sur les dernières vingt-quatre heures, combien d'agents affichent à la fois une latence raisonnable et un taux d'erreur suffisamment bas. Le nombre d'agents en cours d'exécution reflète combien d'agents ont produit une sortie utilisable lors du tout dernier cycle du système. Le nombre d'agents en échec compte le total d'erreurs enregistrées sur la période. La latence moyenne agrège la durée moyenne d'exécution de l'ensemble des agents actifs. Le coût du jour est une estimation en dinars tunisiens des appels aux modèles de langage et du temps de calcul consommés depuis minuit, reconstituée à partir du nombre de cycles exécutés et du nombre d'appels de modèle par cycle.

## Le graphique de performance et de SLA

Ce graphique combine, pour chaque agent, deux informations sur le même repère : sa latence moyenne en secondes, affichée en barres, et son taux de succès en pourcentage, affiché en ligne. Un seuil de qualité de service est implicitement surveillé — un agent dont la latence dépasse plusieurs secondes de façon persistante est considéré comme dégradé, même si son taux d'erreur reste bas, car la lenteur d'un agent peut, dans un système temps réel, être aussi pénalisante qu'une véritable panne.

## Le graphique de santé prédictive

Ce graphique traduit en un score de stabilité, sur cent, la qualité récente du système : il part du taux d'erreurs observé sur une fenêtre glissante des derniers cycles et le convertit en un pourcentage de santé, affiché sur une douzaine de points allant du passé jusqu'à l'instant présent. Un score proche de cent signifie qu'aucune erreur significative n'est survenue récemment ; une chute de ce score signale une dégradation en cours, avant même qu'elle ne devienne visible sous forme de pannes complètes.

## Le graphique d'analyse des coûts

Ce graphique répartit, par agent, le coût estimé de son activité du jour en dinars tunisiens, calculé à partir du nombre d'appels effectués et d'une estimation du coût unitaire d'un appel de modèle de langage. Il permet de repérer si un agent en particulier consomme une part disproportionnée du budget d'exploitation — typiquement le Stratège ou le Coach, qui font appel à des modèles de langage à chaque exécution, contre l'Agent Coach en mode scoring automatique ou l'Agent Décision en mode rapide, qui n'en font pas.

## Le diagramme de flux entre agents

Ce diagramme, en forme de flux qui s'élargit et se rétrécit d'un agent à l'autre, illustre la topologie conceptuelle du système : comment le SupervisorAgent distribue le travail vers l'Analyste et l'Agent d'Analyse d'inventaire, comment ces branches convergent vers le Stratège d'un côté et l'Agent de Décision de l'autre, et comment tout finit par converger vers le RAG puis vers le Coach. Ce diagramme représente l'architecture de conception du système et ne se met pas à jour dynamiquement à partir des journaux d'exécution : il sert de repère visuel constant pour comprendre qui dépend de qui, pas de mesure en temps réel.

## La chronologie d'exécution

Ce diagramme en bandes horizontales reconstitue, pour le tout dernier cycle exécuté par le système, la répartition réelle du temps entre l'Analyste, le RAG, le Stratège et le Coach, bout à bout sur une échelle de secondes. Il permet de voir immédiatement quel agent a consommé la plus grande part du temps total du cycle, information que la latence moyenne seule, agent par agent, ne donne pas aussi clairement.

## La liste des agents et leurs panneaux de détail

Les huit agents sont regroupés par domaine — coaching commercial, inventaire, support partagé — et affichés avec un badge de statut en direct parmi plusieurs états possibles : en direct, actif, terminé, en cours d'exécution, en erreur, au repos, ou en attente. Cliquer sur un agent ouvre un panneau détaillé qui combine deux types d'information de nature très différente.

La description du rôle de l'agent, ses entrées et sorties attendues, ainsi que les champs de l'état partagé qu'il est censé alimenter, constituent une documentation d'architecture statique : ce texte ne change pas d'un cycle à l'autre, il décrit la conception du système telle qu'elle a été pensée, pas ce qui s'est réellement passé à l'instant présent.

À l'inverse, les journaux récents, les métriques affichées — latence moyenne, nombre d'exécutions, nombre d'erreurs, taux de succès — et l'aperçu des dernières entrées et sorties réelles de l'agent, sont entièrement dynamiques : ils reflètent l'activité véritablement enregistrée en base de données pour cet agent, avec un aperçu tronqué du contenu réel qui a transité, plutôt qu'un exemple générique. Pour les agents commerciaux, cet aperçu descend jusqu'au niveau de chaque étape interne de raisonnement ; pour les agents d'inventaire, il reste au niveau d'une exécution complète sur une référence produit, faute d'un détail aussi fin enregistré pour ce domaine.

## Le panneau des incidents Guardrail

Ce panneau recense spécifiquement les décisions du Guardrail qui ne se sont pas soldées par une simple approbation silencieuse : les réécritures demandées, les escalades vers validation humaine, et les blocages purs et simples, chacun avec le conseiller concerné, l'urgence du contexte, l'heure précise et le détail des règles violées. Il combine un historique conservé en base de données, visible dès le chargement de la page, avec un flux en temps réel qui pousse immédiatement tout nouvel incident de blocage ou d'escalade survenant pendant que la page est ouverte, sans attendre un rechargement.

## La section état partagé LangGraph

Cette dernière section affiche, pour le tout dernier cycle exécuté, la valeur réelle de quelques champs clés de l'état partagé décrit dans la documentation d'architecture générale : si les données du point de vente sont bien à jour, quel est l'écart à l'objectif calculé, quel niveau d'urgence a été détecté, combien d'actions le plan stratégique contient, si un conseil final a bien été généré, et si la base de scripts a été exploitée pour ce cycle. C'est la vue la plus directe et la plus littérale de ce que contient réellement l'état partagé à un instant donné.

## Rythme de rafraîchissement

La page se rafraîchit automatiquement toutes les quinze secondes, en interrogeant à nouveau l'ensemble des sources ci-dessus, et un bouton de rafraîchissement manuel permet de forcer immédiatement cette même mise à jour. Ce rythme est volontairement modéré : il suffit à donner une image quasiment en direct de l'activité des agents, sans imposer une charge de requêtes excessive à la base de données à chaque cycle.
