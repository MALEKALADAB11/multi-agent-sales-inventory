# Agent RAG — Connaissance partagée (APP10)

## Rôle

L'Agent RAG n'analyse ni ne décide de rien par lui-même : c'est la mémoire documentaire partagée du système, celle qui permet au Coach et au Stratège de s'appuyer sur des situations de vente déjà vécues et documentées plutôt que d'improviser à chaque fois. Concrètement, il retrouve, parmi une bibliothèque de scripts de vente réels, les quelques situations les plus proches de celle que traverse le conseiller en ce moment, et les met à disposition des agents qui en ont besoin.

Le nom RAG désigne une approche où un modèle de langage ne répond pas uniquement à partir de ce qu'il a appris pendant son entraînement, mais s'appuie explicitement sur des documents retrouvés au moment de la question — ici, des scripts de vente concrets, avec la situation qui les a motivés, l'action recommandée, le produit ciblé, l'argumentaire utilisé et l'impact observé.

## Comment fonctionne la recherche

Lorsqu'un agent a besoin d'un script pertinent — que ce soit l'Agent Stratège en train de construire son plan d'action, ou le CoachAgent en train de répondre à une demande de script ou de gestion d'objection — il fournit une requête en langage naturel qui décrit la situation courante : le type de gap commercial, le contexte horaire, le rayon de produits concerné. Cette requête est transformée en une représentation numérique par un modèle d'embeddings, puis comparée par similarité à l'ensemble des scripts indexés dans la base vectorielle. Les scripts dont la situation documentée ressemble le plus à la requête remontent en tête des résultats.

Un bonus de pertinence est appliqué lorsque la tranche horaire associée au script correspond à l'heure réelle de la demande : un script pensé pour une situation de fin de journée sera favorisé si la question survient effectivement en fin de journée. Un score minimal de pertinence est également exigé : si aucun script ne dépasse ce seuil, l'agent RAG répond honnêtement qu'aucune situation suffisamment proche n'a été trouvée, plutôt que de renvoyer un résultat non pertinent.

## Le contenu de la bibliothèque

Chaque script indexé décrit une catégorie de situation — par exemple un écart critique par rapport à l'objectif sur les terminaux, en matinée, ou un profil de client qui recharge régulièrement et qui pourrait basculer vers un forfait — accompagnée de l'action concrète à mener, du produit ou du bundle ciblé, de l'argumentaire de vente à formuler tel quel auprès du client, de l'impact observé lorsque ce script a été utilisé sur le terrain, et de la plage horaire et du jour de la semaine où il s'applique le mieux. Cette bibliothèque a été constituée à partir de scripts de vente réellement documentés sur le terrain, ce qui donne aux recommandations une crédibilité que n'aurait pas un texte généré librement par un modèle de langage sans ancrage.

## Ce qu'il produit

Pour chaque requête, l'Agent RAG renvoie un petit nombre de scripts les plus pertinents — typiquement les deux ou trois meilleurs plutôt qu'une liste exhaustive — avec, pour chacun, son score de pertinence, sa catégorie de situation, l'action recommandée et l'argumentaire associé. Il indique également, de façon binaire, si le meilleur résultat trouvé est suffisamment pertinent pour être réellement exploité par l'agent appelant, ce qui permet au Stratège comme au Coach de savoir s'ils s'appuient sur une source documentée ou s'ils doivent se rabattre sur leur seul raisonnement.

## Rôle dans la validation Guardrail

Le fait qu'un argumentaire commercial s'appuie ou non sur un script retrouvé par l'Agent RAG est lui-même un signal surveillé par l'Agent Guardrail : lorsque l'écart à combler est important et qu'aucun script pertinent n'a été trouvé, le Guardrail peut demander une réécriture de la réponse, précisément parce qu'une recommandation à fort enjeu commercial sans aucune source documentée est jugée plus risquée.

## Ce qu'on observe dans le Monitoring

Chaque recherche effectuée dans la bibliothèque de scripts est journalisée comme une exécution de l'agent RAG : la requête posée, le nombre de scripts retrouvés, si le résultat a été jugé suffisamment pertinent pour être utilisé, et le score du meilleur résultat. C'est cette télémétrie qui permet à la page de supervision de suivre l'activité réelle du RAG au fil des échanges, plutôt qu'un agent qui semblerait éternellement silencieux alors qu'il est en réalité sollicité à chaque script demandé.
