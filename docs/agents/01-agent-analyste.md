# Agent Analyste (APP02)

## Rôle

L'Agent Analyste est le premier maillon de la branche de coaching commercial. Sa mission est de produire, à chaque cycle, une photographie complète et à jour de la performance du magasin : où en est le chiffre d'affaires par rapport à l'objectif du jour, quelle est la prévision de fin de journée, à quel point la situation est urgente, et si des anomalies ou des tendances particulières se dessinent dans les ventes de la journée ou des jours précédents.

Ce qui distingue cet agent d'un simple calcul automatisé, c'est qu'il fonctionne selon le schéma Observation-Raisonnement-Action : plutôt que de suivre une séquence figée d'étapes, il dispose d'une palette d'une dizaine d'outils d'analyse et décide lui-même, cycle après cycle, lesquels appeler et dans quel ordre, en fonction de ce qu'il observe au fur et à mesure. Un nombre maximal d'itérations est fixé pour garantir que le raisonnement se termine dans un délai raisonnable.

## Déclenchement

L'Agent Analyste est le tout premier agent exécuté dans la branche commerciale, à chaque cycle du système, que ce déclenchement soit un cycle planifié automatique, la connexion d'un conseiller au tableau de bord, un message posé au CoachAgent, ou un appel manuel.

## Ce qu'il reçoit

Il part de l'identifiant du cycle et du magasin concerné, de l'objectif journalier de chiffre d'affaires et de l'heure courante. Il a accès aux données du point de vente en direct — chiffre d'affaires cumulé du jour, nombre de transactions, panier moyen — ainsi qu'à l'historique récent des transactions du magasin. Il dispose également de la dernière prévision de fin de journée calculée par le moteur de prévision, et de l'historique de feedback des cycles précédents, qui constitue une forme de mémoire lui permettant de situer la journée actuelle par rapport aux tendances passées.

## Comment il raisonne

Le premier appel, systématique, consiste à récupérer l'état du point de vente en direct. À partir de là, l'agent peut choisir parmi plusieurs familles d'outils : le calcul de la prévision de fin de journée par un ensemble de méthodes complémentaires (projection linéaire, ajustement saisonnier, vélocité récente, modèle de prévision de séries temporelles) ; le calcul de l'écart en temps réel par rapport à l'objectif, avec un score d'urgence et une évaluation de la faisabilité d'atteindre l'objectif dans le temps restant ; l'analyse de la tendance intrajournalière, qui mesure la vélocité et l'accélération des ventes, calcule un score d'écart-type pour l'heure en cours et identifie les heures de pointe ; la comparaison avec le même jour de la semaine sur les quatre semaines précédentes ; la détection d'anomalies par écart-type sur vingt-huit jours de données horaires ; une décomposition de série temporelle façon tendance-saisonnalité-résidu, avec calcul d'autocorrélation à sept jours ; une prévision à plusieurs horizons — heure suivante, trois heures suivantes, fin de journée, même jour la semaine prochaine — assortie d'un intervalle de confiance ; la récupération des alertes de stock actives sur le magasin ; la récupération du contexte saisonnier, c'est-à-dire l'effet estimé des événements du marché sur la demande ; et enfin l'analyse de la vélocité produit par référence, croisée avec les niveaux de stock, pour estimer combien de jours restent avant une éventuelle rupture.

## Ce qu'il produit

En sortie, l'Agent Analyste fournit l'écart à l'objectif en pourcentage et en montant, le niveau et le score d'urgence, la prévision de fin de journée avec son intervalle de confiance, un résumé d'analyse rédigé en langage naturel par le modèle de langage, un signal de tendance accompagné du facteur saisonnier et de l'autocorrélation à sept jours, un indicateur signalant si la journée est atypique par rapport à l'historique, un instantané du nombre de produits en situation de stock critique, et enfin une requête de recherche destinée à la branche Connaissance, construite à partir de son propre diagnostic, qui servira à retrouver des scripts de vente pertinents pour la situation détectée.

## Modèle utilisé et robustesse

Cet agent s'appuie sur un modèle de langage exécuté localement, choisi après comparaison pour ce rôle précis : parce que sa boucle de raisonnement peut enchaîner jusqu'à quatre appels successifs au modèle, un modèle local rapide est préférable à un grand modèle distant dont la latence réseau s'additionnerait à chaque itération. Si le modèle de langage échoue ou n'est pas disponible, un résumé de secours est généré uniquement à partir des données chiffrées déjà calculées — écart, urgence, chiffre d'affaires, prévision — sans jamais bloquer le cycle.

## Ce qu'on observe dans le Monitoring

L'Agent Analyste écrit sa télémétrie d'exécution dans le journal général des agents du domaine commercial : chaque appel d'outil et chaque étape de raisonnement y laisse une trace horodatée, avec sa durée et son statut. C'est ce journal qui alimente son statut en direct, sa latence moyenne, son taux de succès et le détail de ses dernières exécutions sur la page de supervision.
