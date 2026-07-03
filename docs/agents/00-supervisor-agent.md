# SupervisorAgent — l'orchestrateur central

## Rôle

Le SupervisorAgent n'est pas un agent métier comme les autres : c'est le chef d'orchestre qui exécute un cycle complet du système et coordonne les huit agents spécialisés. Il ne produit lui-même aucune analyse ni aucune recommandation — son travail consiste à démarrer les bonnes branches de traitement dans le bon ordre, à fusionner leurs résultats, à faire respecter le verdict du Guardrail, et à garantir qu'un cycle se termine proprement même si une partie du système échoue.

C'est le point d'entrée unique de tout le système, que ce soit pour un cycle planifié automatique, pour la connexion d'un conseiller au tableau de bord, pour un message envoyé au CoachAgent, ou pour un événement stock critique détecté ailleurs dans le système.

## Le RetailState — l'état partagé

Tous les agents communiquent à travers un seul objet d'état partagé, construit et initialisé par le SupervisorAgent au début de chaque cycle, plutôt que de s'échanger des messages point à point. Chaque agent lit les champs dont il a besoin et écrit uniquement dans ses propres champs de sortie. Si un agent échoue ou n'est pas invoqué, ses champs restent simplement vides, et les agents suivants dans le cycle traitent cette absence comme "aucune donnée disponible" sans jamais bloquer la suite du cycle. Cette conception rend le système tolérant aux pannes partielles : une branche qui échoue ne fait pas échouer les autres.

L'état partagé regroupe l'identification du cycle (identifiant unique, magasin concerné, conseiller éventuellement concerné, type de déclencheur), les données brutes reçues en entrée (flux du point de vente, niveaux de stock, contexte externe, message du conseiller), les sorties de chacune des quatre branches parallèles, les sorties de l'Agent Coach et de l'Agent Guardrail, l'état de la validation humaine si elle a été déclenchée, ainsi que des informations d'observabilité : quels agents ont réellement été invoqués ce cycle, la latence totale, le détail des métriques par étape, et la liste des erreurs rencontrées.

## Déroulement d'un cycle

Le cycle suit une séquence en plusieurs temps. D'abord, l'état partagé est initialisé avec un identifiant de cycle et les structures de suivi vides. Ensuite, quatre branches démarrent simultanément à partir de cet état initial : la branche Sales, qui enchaîne l'Agent Analyste puis l'Agent Stratège ; la branche Connaissance, qui interroge la base vectorielle de scripts de vente ; la branche Contexte, qui récupère les signaux externes comme la météo, les jours fériés et les événements commerciaux ; et la branche Inventory, qui exécute le pipeline complet d'analyse de stock sur l'ensemble des références suivies par le magasin.

Une fois que ces quatre branches sont terminées, leurs résultats respectifs sont fusionnés dans l'état partagé, et un résumé de cette fusion est journalisé : écart à l'objectif, niveau d'urgence, nombre de décisions de stock produites, nombre de scripts de vente exploités, nombre d'erreurs rencontrées sur l'ensemble des branches.

Vient ensuite l'Agent Coach, qui combine les sorties de la branche Sales et de la branche Inventory pour produire une recommandation de produit unique, chiffrée et justifiée. Cette recommandation est immédiatement soumise à l'Agent Guardrail, qui l'évalue contre ses règles métier et rend un verdict.

Le SupervisorAgent applique alors un routage conditionnel selon ce verdict. Si la recommandation est approuvée, elle est notifiée telle quelle au tableau de bord. Si le Guardrail demande une réécriture, l'état repart vers l'Agent Coach avec le motif précis du refus, pour une nouvelle tentative — une seule itération supplémentaire est autorisée, afin d'éviter une boucle infinie. Si le Guardrail escalade la décision, elle est mise en file d'attente de validation humaine avant toute diffusion. Si le Guardrail bloque purement et simplement la recommandation, elle est remplacée par un message de repli sécurisé avant d'être notifiée.

Enfin, le résultat complet du cycle est sauvegardé en mémoire, pour alimenter l'historique et l'apprentissage de l'Agent Analyste lors du cycle suivant, et diffusé en temps réel au tableau de bord via la connexion WebSocket.

## Pourquoi cette architecture

Le choix d'un état partagé unique plutôt que d'échanges de messages point à point simplifie considérablement le raisonnement sur le système : à tout moment, l'état complet d'un cycle est inspectable dans un seul objet, ce qui facilite le débogage, la reprise après erreur partielle, et l'observabilité. Le dispatch en quatre branches parallèles réduit la latence totale du cycle, puisque les branches Connaissance, Contexte et Inventory ne dépendent pas les unes des autres et peuvent s'exécuter en même temps que la branche Sales.

Le passage systématique par l'Agent Guardrail avant toute diffusion est le trait distinctif du système par rapport à un simple assistant conversationnel : aucune recommandation générée automatiquement n'atteint un conseiller ou un manager sans avoir été validée contre des règles métier explicites.

## Ce qu'on observe dans le Monitoring

Le SupervisorAgent lui-même n'apparaît pas comme une ligne séparée dans la page de supervision technique, car il ne produit pas de télémétrie propre — il orchestre les agents qui, eux, écrivent leurs journaux d'exécution. C'est en revanche lui qui détermine quels agents ont été réellement invoqués sur un cycle donné, information qui alimente le bloc "Raisonnement Multi-Agent" visible sur le tableau de bord et la section correspondante de la page de supervision.
