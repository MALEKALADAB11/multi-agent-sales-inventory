# Agent Guardrail (APP08)

## Rôle

L'Agent Guardrail est le garde-fou transversal du système : sa seule mission est d'empêcher qu'une recommandation ou une réponse générée automatiquement n'atteigne un conseiller ou un manager si elle viole une règle métier. C'est ce mécanisme, plus que toute autre partie du système, qui distingue l'ensemble d'un simple assistant conversationnel : chaque sortie produite par l'Agent Coach — qu'elle vienne du calcul de recommandation automatique ou d'une réponse conversationnelle en direct — est systématiquement validée avant diffusion, jamais après coup.

## Déclenchement

Le Guardrail intervient à deux moments distincts mais avec la même logique. Dans le cycle orchestré par le SupervisorAgent, il s'exécute juste après l'Agent Coach, avant toute notification au tableau de bord. Dans la conversation en direct avec le CoachAgent, il est invoqué en ligne à chaque réponse envoyée à un conseiller, sans exception.

## Ce qu'il évalue

Le Guardrail reçoit la recommandation ou la réponse à valider — le produit éventuellement poussé, le message rédigé, le score de confiance associé — l'état du stock du produit concerné, un indicateur signalant si la réponse s'appuie sur un script retrouvé par l'Agent RAG, et le coût de la commande d'inventaire associée le cas échéant.

Il évalue alors sept règles indépendantes, chacune associée à un niveau de sévérité propre. La première règle interdit purement et simplement de recommander un produit dont le stock est nul : c'est un motif de blocage immédiat. La deuxième règle surveille les produits dont le stock s'épuise dans un délai critique, à moins qu'il ne s'agisse justement d'un déstockage volontaire assumé ; dans ce cas, la réponse est renvoyée pour réécriture plutôt que bloquée. La troisième règle porte sur la source d'un argumentaire commercial : lorsque l'écart à combler est important et qu'aucun script pertinent n'a été retrouvé par l'Agent RAG, une réécriture est demandée, car un argumentaire à fort enjeu sans aucune source documentée est jugé plus risqué. La quatrième règle recherche, dans le texte généré, des motifs évoquant une remise ou une offre commerciale non autorisée, ce qui déclenche un blocage immédiat. La cinquième règle interdit de recommander une offre réseau mobile ou fibre sans confirmation préalable de l'éligibilité du client, sous peine de réécriture. La sixième règle surveille le score de confiance global de la recommandation : s'il descend sous un seuil configuré, la décision est automatiquement escaladée vers une validation humaine plutôt que diffusée telle quelle. La septième et dernière règle porte sur le budget : une commande de réapprovisionnement dont le coût dépasse un plafond configuré nécessite systématiquement la validation d'un manager avant d'être exécutée.

Le statut final retenu par le Guardrail est toujours le plus sévère parmi l'ensemble des problèmes détectés simultanément, selon l'ordre suivant, du plus grave au moins grave : blocage, puis escalade, puis réécriture, puis approbation.

## Ce qu'il produit

En sortie, le Guardrail livre un statut unique parmi quatre possibles — approuvé, réécriture demandée, escalade vers validation humaine, ou bloqué — accompagné de la liste précise des problèmes détectés, chacun associé à la règle concernée et à un message explicatif compréhensible. Lorsqu'une réécriture est demandée, une instruction précise est transmise à l'Agent Coach pour orienter sa nouvelle tentative. Lorsque la réponse est bloquée, un message de repli sécurisé est fourni pour remplacer la réponse initiale, plutôt que de renvoyer une erreur brute au conseiller.

## Modèle utilisé

Le Guardrail ne fait appel à aucun modèle de langage : c'est une logique entièrement déterministe, fondée sur des règles et des seuils configurables — seuil de confiance minimal, plafond budgétaire, délai critique de rupture de stock. Ce choix est délibéré : un garde-fou de sécurité doit rester prévisible et vérifiable, ce qu'un modèle de langage ne garantit pas de la même façon.

## Ce qu'on observe dans le Monitoring

Chaque évaluation du Guardrail — qu'elle survienne dans le cycle automatique ou lors d'un échange conversationnel — est journalisée avec le statut retenu, les problèmes détectés et le conseiller concerné. Les incidents les plus significatifs, c'est-à-dire les réécritures, les escalades et les blocages, alimentent un panneau dédié sur la page de supervision qui recense les incidents récents, avec un compteur pour chaque type d'incident. Les événements de blocage et d'escalade sont en outre poussés en temps réel vers le tableau de bord dès qu'ils surviennent, pour qu'un manager puisse réagir immédiatement plutôt que de découvrir l'incident a posteriori.
