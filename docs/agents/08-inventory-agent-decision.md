# Inventory Decision Agent (INV-D)

## Rôle

L'Agent Décision est la couche de jugement final du pipeline d'inventaire. Il reçoit le diagnostic établi par l'Agent Analyse et le signal de demande produit par l'Agent Contexte, et les transforme en une décision opérationnelle unique, explicable et directement actionnable par un responsable de magasin. C'est cet agent qui incarne l'exigence du projet d'aller au-delà d'un simple calcul mécanique de réapprovisionnement : il ne se contente pas de recalculer une formule, il rend un jugement qui tient compte du contexte, des contraintes budgétaires et de la situation particulière du produit.

## Ce qu'il reçoit

L'agent dispose du rapport complet de l'Agent Analyse — niveau de risque, jours de stock restants, point de commande, quantité suggérée par la formule, coût, contraintes de quantité minimale imposées par le fournisseur — du rapport de l'Agent Contexte — pourcentage d'ajustement de la demande, signal dominant, niveau de confiance associé — de l'ensemble des métriques de stock recalculées en appliquant cet ajustement de demande, et enfin de l'objectif business actuellement actif pour le magasin.

## Comment il raisonne

Sur la base de l'ensemble de ces éléments, l'agent détermine l'action à entreprendre parmi quatre possibilités : commander, expédier en urgence, surveiller la situation sans agir immédiatement, ou ne rien faire. Il détermine ensuite si la quantité calculée mécaniquement par la formule doit être suivie telle quelle ou ajustée à la lumière du contexte. Il évalue si une escalade vers un décideur humain est nécessaire, ce qui est le cas lorsque le coût de la commande dépasse un seuil configuré, lorsque le produit est en fin de vie avec un risque critique de rupture, lorsque l'objectif business en vigueur entre en conflit significatif avec la décision la plus logique du point de vue du coût, ou plus simplement lorsque le modèle exprime une incertitude réelle sur sa propre décision.

Cet agent formule également sa décision en langage naturel, à la manière d'un acheteur senior qui s'adresserait directement à un responsable de magasin : l'action retenue et le chiffre clé sont annoncés en premier, suivis de la contrainte physique ou financière qui la justifie, puis de l'influence du signal de demande contextuel si elle est pertinente, et enfin de toute alerte particulière qu'il convient de signaler.

Une étape d'auto-critique relit ensuite la décision produite. Si cette relecture identifie un problème, une révision est tentée en fournissant au modèle le motif précis du refus ; si cette seconde tentative échoue à son tour, l'agent se replie sur un ensemble de règles déterministes strictement équivalentes, garantissant qu'une décision — même prudente — est toujours rendue plutôt que de laisser le cycle sans réponse.

Selon le mode d'exécution retenu pour le cycle, ce raisonnement peut s'appuyer intégralement sur un modèle de langage à large contexte via une passerelle distante, ou se reposer directement sur l'ensemble de règles déterministes équivalent lorsque le mode rapide est privilégié pour limiter la latence globale du cycle, par exemple pour alimenter un tableau de bord en temps réel traitant l'ensemble des références d'un magasin.

## Ce qu'il produit

Pour chaque référence traitée, l'Agent Décision livre l'action retenue, la quantité de commande associée, le niveau d'urgence — immédiat, cette semaine, ce mois-ci, ou aucune urgence — une justification de la décision et de la quantité choisie, un niveau de confiance dans cette décision, le compromis assumé qu'elle implique, c'est-à-dire le coût ou le risque que le magasin accepte consciemment en la suivant, un indicateur et le motif d'une éventuelle escalade vers un humain, et enfin le texte de recommandation en langage naturel destiné directement à l'opérateur du magasin.

## Ce qu'on observe dans le Monitoring

Chaque décision rendue par cet agent, pour chaque référence traitée, est enregistrée comme une exécution dans le journal des agents d'inventaire, avec son statut, sa durée, et un résumé chiffré du résultat produit — nombre d'éléments traités, nombre d'alertes générées, nombre de recommandations produites. Cette télémétrie alimente en direct son statut, sa latence moyenne et son taux de succès sur la page de supervision.
