# Agent Stratège (APP05)

## Rôle

L'Agent Stratège prend le relais immédiatement après l'Agent Analyste. Là où l'Analyste établit un diagnostic — où en est le magasin, quel est l'écart, quelle est l'urgence — le Stratège transforme ce diagnostic en un plan d'action concret : quelles actions entreprendre, quels produits mettre en avant, avec quel argumentaire, et quel message faire remonter au manager du magasin. C'est l'agent qui donne du sens opérationnel à l'analyse brute, en la confrontant au contexte du moment et à ce qui a déjà fonctionné par le passé.

## Déclenchement

Le Stratège s'exécute juste après l'Agent Analyste, à l'intérieur de la même branche commerciale, et reçoit en entrée l'intégralité de la sortie de l'Analyste : écart, urgence, résumé, prévisions, données du point de vente.

## Comment il construit son plan

Son traitement se déroule en six étapes. Il commence par récupérer le contexte complet du magasin : la météo du moment, les événements commerciaux des concurrents à proximité, et un bilan de portabilité numérique qui donne une idée du mouvement de clients entrant ou sortant. Il interroge ensuite la base vectorielle de scripts de vente, en utilisant la requête de recherche construite par l'Analyste à partir de son diagnostic, pour retrouver des situations passées similaires et les argumentaires qui avaient fonctionné. Il synthétise alors ces éléments — météo, alertes, scripts retrouvés — en une cause racine, c'est-à-dire une explication probable de l'écart observé. Il fait ensuite appel au modèle de langage pour générer un plan d'action structuré à partir de l'ensemble de ce contexte. Une étape d'auto-critique évalue la qualité de ce plan généré : si les actions proposées manquent de produit cible clairement identifié ou d'argumentaire de vente convaincant, un signal de validation humaine peut être levé. Enfin, si la réponse du modèle de langage s'avère tronquée ou mal formée, une extraction de secours par reconnaissance de motifs textuels permet de récupérer ce qui peut l'être plutôt que d'perdre tout le travail déjà fait.

## Ce qu'il produit

En sortie, l'Agent Stratège livre une synthèse stratégique en texte libre, une liste d'actions priorisées — chacune associée à un produit cible précis et à un argumentaire de vente rédigé — une cause racine de l'écart identifié, une liste de produits à mettre en avant en priorité, un message rédigé spécifiquement pour le manager du magasin, une carte de chaleur contextuelle qui croise trafic, stock, événements, réseau et météo par tranche horaire, des alertes en temps réel, un indicateur signalant si la base de scripts de vente a effectivement été exploitée avec le nombre de scripts utilisés, ainsi que le score et le statut de sa propre auto-critique.

## Modèle utilisé et robustesse

Le Stratège s'appuie sur un modèle de langage accédé via une passerelle distante, à large fenêtre de contexte : contrairement à l'Analyste qui enchaîne plusieurs petits appels, le Stratège effectue un seul appel complexe qui doit tenir compte de beaucoup d'informations contextuelles simultanément, ce qui justifie un modèle plus capable même au prix d'une latence réseau plus élevée. En cas d'échec complet de cette génération, l'agent se replie sur un plan vide, où la cause racine se limite à l'écart chiffré déjà connu — le cycle continue sans plan d'action détaillé plutôt que d'échouer entièrement.

## Ce qu'on observe dans le Monitoring

Comme l'Analyste, le Stratège journalise chacune de ses six étapes de traitement dans le journal général des agents : ces entrées permettent de suivre en direct son statut, sa latence, son taux de succès, ainsi que le contenu réel de ses dernières entrées et sorties sur la page de supervision.
