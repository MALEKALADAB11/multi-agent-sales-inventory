# Inventory Context Agent (INV-C)

## Rôle

L'Agent Contexte s'exécute en parallèle de l'Agent Analyse, sur la même référence produit. Là où l'Analyse établit un diagnostic froid à partir des seules données de stock et de coût, l'Agent Contexte enrichit ce diagnostic avec tout ce qui, dans l'environnement immédiat du magasin, pourrait faire varier la demande de ce produit dans les jours qui viennent : une promotion en cours, un changement de météo, un jour férié approchant, un événement de marché, ou simplement une tendance historique propre à cette catégorie de produit dans ce magasin précis.

## Ce qu'il reçoit

L'agent part simplement de l'identifiant de la référence produit et du magasin concerné : c'est à lui d'aller chercher activement les signaux pertinents plutôt que de les recevoir en entrée.

## Comment il raisonne

Il récupère cinq familles de signaux, en parallèle pour limiter la latence : la catégorie du produit, les motifs historiques de demande déjà observés pour cette catégorie dans ce magasin, les promotions actives ou sur le point de démarrer dans les sept prochains jours et spécifiques à ce produit, la météo locale et son effet estimé sur la fréquentation du magasin, les jours fériés à venir, et les événements de marché à venir pertinents pour cette catégorie de produit.

Les signaux communs à l'ensemble d'un magasin — météo, jours fériés, événements — sont mis en cache pendant une heure, plutôt que d'être recalculés à l'identique pour chacune des centaines de références traitées lors d'un même cycle : sans cette précaution, le système effectuerait des centaines d'appels strictement identiques en quelques secondes, ce qui pénaliserait inutilement la latence globale sans apporter la moindre information supplémentaire.

## Ce qu'il produit

En sortie, l'Agent Contexte fournit un pourcentage d'ajustement de la demande, à la hausse ou à la baisse, par rapport à la prévision de base établie par l'Agent Analyse. Il identifie également le signal dominant parmi ceux qu'il a examinés — s'agit-il avant tout d'une promotion, d'un effet météo, d'un événement particulier, ou simplement d'une tendance historique récurrente, ou bien aucun signal notable ne se dégage-t-il ce cycle-ci. Il fournit un niveau de confiance associé à ce signal dominant, ainsi qu'une interprétation textuelle synthétisant l'ensemble des signaux examinés, de façon à ce que l'Agent Décision, qui reçoit ce rapport, puisse comprendre non seulement le chiffre d'ajustement mais aussi sa justification.

## Ce qu'on observe dans le Monitoring

Comme l'Agent Analyse, chaque exécution complète de l'Agent Contexte sur une référence donnée est enregistrée dans le journal des agents d'inventaire avec son statut, sa durée et un résumé du résultat produit, ce qui permet de suivre son activité et sa fiabilité en direct sur la page de supervision.
