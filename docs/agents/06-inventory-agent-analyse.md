# Inventory Analysis Agent (INV-A)

## Rôle

L'Agent Analyse ouvre le pipeline de la branche Inventory. Sa mission est d'établir, pour une référence produit donnée dans un magasin donné, un diagnostic factuel et chiffré de sa situation de stock : à quel point le risque de rupture ou de surstock est-il élevé, quelles sont les métriques de réapprovisionnement associées, et quelle prévision de demande de base peut-on en tirer. C'est le socle sur lequel s'appuieront ensuite l'Agent Contexte et l'Agent Décision.

Ce pipeline s'exécute par lot sur l'ensemble des références suivies par un magasin, ce qui peut représenter une centaine à quelques centaines de produits par cycle : c'est pourquoi les données lourdes, comme les niveaux de stock courants, sont pré-chargées en un seul lot pour tout le magasin, plutôt que d'être récupérées une par une pour chaque référence.

## Ce qu'il reçoit

L'agent part de l'identifiant de la référence produit et du magasin concerné, ainsi que de l'objectif business actuellement actif — un magasin peut privilégier la réduction des coûts, le niveau de service client, ou un équilibre entre les deux. Il dispose du niveau de stock courant, du coût unitaire du produit, de la quantité minimale de commande imposée par le fournisseur, du délai de livraison moyen et de sa variabilité, ainsi que de l'étape du cycle de vie du produit, information utile pour distinguer un produit en fin de vie d'un produit en pleine phase de croissance.

## Comment il raisonne

Le traitement se déroule en trois étapes. La première consiste à rassembler les données nécessaires : stock courant, informations produit, historique de ventes. La deuxième calcule l'ensemble des métriques classiques de réapprovisionnement : le nombre de jours de stock restants au rythme de vente actuel, le point de commande à partir duquel il faudrait déjà avoir passé une nouvelle commande, la quantité économique de commande qui minimise le coût total, le stock de sécurité recommandé pour absorber les aléas de la demande, le coût total de réapprovisionnement associé, et le niveau de service effectivement atteint avec la politique actuelle.

La troisième étape est un travail de raisonnement qui valide ou ajuste le niveau de risque calculé mécaniquement par ces règles. Selon le mode d'exécution du cycle, ce raisonnement peut s'appuyer sur un modèle de langage pour affiner le jugement, ou se replier sur un calcul purement déterministe équivalent lorsque le mode rapide est privilégié — typiquement lorsque la latence du cycle est contrainte, par exemple pour alimenter un tableau de bord en temps réel où l'ensemble des références du magasin doit être traité rapidement.

## Ce qu'il produit

En sortie, l'Agent Analyse livre un niveau de risque parmi quatre paliers — faible, moyen, élevé, critique — accompagné d'une justification, l'ensemble des métriques de réapprovisionnement calculées, un signalement explicite si la situation de ce produit entre en conflit avec l'objectif business actuellement actif du magasin, et un indicateur de surstock lorsque c'est le cas contraire du risque de rupture qui se pose.

## Ce qu'on observe dans le Monitoring

Contrairement aux agents de la branche commerciale, l'Agent Analyse ne journalise pas le détail de chaque étape interne, mais chaque exécution complète — un passage sur une référence donnée — est enregistrée comme une exécution dans le journal des agents d'inventaire, avec son statut, sa durée, le nombre d'éléments traités et le nombre d'alertes ou de recommandations qui en ont résulté. C'est cette granularité, par exécution plutôt que par étape détaillée, qui alimente son statut, sa latence moyenne et son taux de succès sur la page de supervision.
