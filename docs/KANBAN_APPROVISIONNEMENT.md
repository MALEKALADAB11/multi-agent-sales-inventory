# Le Kanban d'approvisionnement — pourquoi et comment

Ce document explique, en texte, le raisonnement qui a conduit à introduire un tableau
Kanban dans le système multi-agents Ooredoo, la manière dont la méthode Kanban a été
traduite en choix techniques concrets, et les limites assumées de cette implémentation.
Il ne contient volontairement aucun extrait de code : il s'adresse à un lecteur qui veut
comprendre la logique produit et la logique d'ingénierie, pas relire l'implémentation.

---

## 1. Le problème que le Kanban vient résoudre

Le système comporte une chaîne agentique complète côté inventaire : un agent d'analyse
lit l'historique de ventes, un agent de prévision projette la demande, et un agent de
décision conclut par une action — réapprovisionner (ORDER), accélérer une commande
existante (EXPEDITE), transférer entre magasins, ou ne rien faire. Cette chaîne
produisait des recommandations parfaitement calculées, mais elle s'arrêtait là.

Trois problèmes concrets en découlaient.

**Premier problème : la recommandation n'était pas un objet vivant.** Une ligne dans
la table des recommandations est un verdict figé à un instant t. Rien ne dit si un
humain l'a vue, si elle a été transformée en bon de commande, si le fournisseur a
confirmé, si la marchandise est arrivée. L'information de « où en est-on » n'existait
nulle part, ni pour le responsable magasin, ni pour les agents eux-mêmes.

**Deuxième problème : le travail était invisible.** Un approvisionnement, dans la vraie
vie, n'est pas un événement mais un processus qui s'étale sur des jours ou des semaines :
on rédige, on soumet au fournisseur, on attend la confirmation, l'expédition, la
réception. Pendant tout ce temps, la commande occupe de la trésorerie, de l'attention,
et de la capacité de traitement. Sans représentation visuelle, ce travail en cours
n'existe que dans la tête des gens. On oublie les commandes bloquées, on relance deux
fois, on recommande un produit déjà commandé.

**Troisième problème : la boucle n'était pas fermée.** Tant que la réception de la
marchandise ne remontait pas dans les niveaux de stock, l'agent de décision continuait
à voir un stock bas et à re-suggérer indéfiniment le même réapprovisionnement. Un agent
qui ne voit pas les conséquences de ses propres décisions produit du bruit, pas de la
valeur.

Le Kanban répond exactement à ces trois problèmes, parce que c'est précisément ce pour
quoi la méthode a été conçue : rendre visible un flux de travail invisible, en
comprendre les blocages, et fermer les boucles de rétroaction.

---

## 2. Pourquoi Kanban plutôt qu'autre chose

Plusieurs représentations étaient envisageables : une simple liste de bons de commande,
un tableau filtrable, un diagramme de Gantt, un workflow de type BPMN. Le Kanban a été
retenu pour des raisons qui tiennent autant au métier qu'à l'architecture logicielle.

**Le processus d'achat est déjà un flux à états.** Un bon de commande passe par des
étapes discrètes, ordonnées, et mutuellement exclusives : il est brouillon, ou soumis,
ou confirmé, ou expédié, ou reçu. C'est exactement la définition d'une colonne Kanban.
La méthode n'a pas été plaquée sur le domaine ; elle épousait déjà sa forme naturelle.
Modéliser autrement aurait demandé de tordre soit le métier, soit l'outil.

**Kanban ne demande pas de réorganiser le travail existant.** C'est le principe fondateur
de la méthode : « commencez par ce que vous faites aujourd'hui ». Le processus d'achat
d'Ooredoo n'a pas été redessiné pour entrer dans l'outil. Le tableau s'est contenté de
rendre visibles des états qui existaient déjà, de manière implicite, dans les habitudes
des acheteurs. C'était un argument décisif : le projet ne pouvait pas se permettre
d'imposer une conduite du changement en même temps qu'un système d'IA.

**Le Kanban est un système à flux tiré, et c'est ce que le HITL exige.** Le projet repose
sur une philosophie de garde-fou humain (Human-In-The-Loop) déjà présente ailleurs :
l'agent Guardrail, le routeur HITL. Un système poussé — l'agent décide, la commande part —
était exclu, parce qu'un bon de commande est un engagement financier. Un système tiré —
l'agent propose, l'humain tire la carte dans le flux — correspond exactement au geste
métier attendu. Le Kanban donne à cette philosophie une forme visuelle immédiatement
compréhensible : rien n'avance sans que quelqu'un ne le fasse avancer.

**Le Kanban est un artefact partagé entre l'humain et les agents.** C'est peut-être la
raison la plus structurante. Le tableau n'est pas seulement une interface : c'est un
état du monde, lisible par le responsable magasin comme par un agent LLM. Quand l'agent
analyste veut savoir si une rupture de stock est réellement critique, il consulte le
tableau ; s'il y voit une commande déjà expédiée pour ce produit, il sait que l'alerte
pèse moins lourd. Une liste plate n'aurait pas offert cette sémantique. Les colonnes
portent du sens métier, et ce sens est directement exploitable dans un raisonnement.

---

## 3. Comment les principes Kanban ont été traduits

### 3.1 Visualiser le flux : les colonnes sont les états réels du métier

Le tableau comporte sept colonnes de flux, plus une colonne d'entrée particulière.
Chacune correspond à un statut réel d'un bon de commande, et non à une catégorie
inventée pour l'écran.

- **Suggéré (IA)** — l'agent de décision a conclu qu'il fallait commander. Rien n'est
  encore engagé. C'est la file d'attente d'entrée du système, alimentée par la machine.
- **Brouillon** — un humain a validé le principe de la commande. Elle existe, mais n'est
  pas partie chez le fournisseur.
- **Soumis** — la commande a été transmise au fournisseur ; on attend sa réponse.
- **Confirmé** — le fournisseur s'est engagé. Le délai de livraison court.
- **Expédié** — la marchandise a quitté le fournisseur.
- **Reçu partiellement** — une partie seulement de la quantité commandée est arrivée.
- **Reçu** — la commande est close, la marchandise est en stock.

À ces colonnes s'ajoute un état transverse, **Litige**, et un état terminal, **Annulé**.
Le litige mérite une mention particulière : dans la théorie Kanban, on ne cache pas les
problèmes dans les colonnes normales, on les rend visibles. Une commande en litige est
donc extraite du flux principal et affichée dans une zone dédiée, exactement comme un
« bloqué » sur un tableau physique. Ce n'est pas un état d'échec définitif : une commande
en litige peut revenir dans le flux si le problème est résolu.

### 3.2 Rendre les règles du processus explicites

Un tableau Kanban n'a de valeur que si les mouvements autorisés reflètent la réalité du
processus. Le système définit donc une machine à états : depuis chaque colonne, seul un
petit ensemble de destinations est légal. Une commande ne peut pas passer de « brouillon »
à « reçu » sans être passée par le fournisseur ; une commande reçue ne peut plus bouger,
elle est terminale ; une commande annulée non plus.

Cette contrainte est appliquée dans le dépôt de données, au plus près de la base, et non
dans l'interface. C'est un choix délibéré : les cartes peuvent être déplacées depuis
l'écran Angular, mais aussi par un agent via l'outil MCP, ou par un futur script
d'intégration fournisseur. Si la règle vivait dans le composant Angular, chacun de ces
chemins pourrait la contourner. En la plaçant dans la couche d'accès aux données, elle
devient une propriété du domaine, pas une propriété de l'écran. L'interface se contente
de refuser visuellement les dépôts illégaux — elle rend la règle agréable, elle ne la
définit pas.

### 3.3 La colonne « Suggéré » : la porte d'entrée humaine

C'est la spécificité de ce Kanban par rapport à un tableau d'achat classique, et le point
où la méthode rencontre le système multi-agents.

Lorsque l'agent de décision conclut à un ORDER ou un EXPEDITE, il ne crée pas seulement
une recommandation en base : il fabrique immédiatement une carte dans la colonne
« Suggéré ». Cette carte porte des attributs propres à son origine machine — la source
(agent), le degré d'urgence, un indice de confiance, et une référence vers la décision
de l'agent qui l'a produite. Le responsable magasin voit donc apparaître, en temps réel,
ce que le système lui propose de faire, avec le niveau de certitude associé.

Ces cartes ne sont **pas déplaçables au glisser-déposer**. C'est un choix qui peut
surprendre sur un tableau Kanban, où le drag-and-drop est le geste canonique. La raison
est double.

D'abord, une raison métier : sortir une carte de « Suggéré » n'est pas un changement de
colonne, c'est une **décision**. Approuver, c'est engager de l'argent. Le geste doit être
explicite, nommé, et attribuable à une personne — d'où deux boutons, « Approuver » et
« Rejeter », plutôt qu'un glissement qu'on peut faire par inadvertance.

Ensuite, une raison technique : approuver une suggestion touche deux tables à la fois.
La recommandation de l'agent passe à « approuvée » et le bon de commande passe de
« suggéré » à « brouillon », dans une seule et même transaction. Rejeter fait
symétriquement passer la recommandation à « rejetée » et le bon de commande à « annulé ».
Un simple changement de statut, tel que le produirait un glisser-déposer générique,
casserait cette cohérence : on se retrouverait avec un bon de commande vivant rattaché à
une recommandation encore en attente. La colonne « Suggéré » est donc, par construction,
une porte à sens unique gardée par un humain — et cette garde est maintenue quel que soit
le chemin d'accès, y compris l'outil MCP, qui refuse explicitement de déplacer une carte
suggérée.

### 3.4 Limiter et révéler le travail en cours

Le Kanban orthodoxe impose des limites chiffrées de travail en cours par colonne. Le
projet n'a pas imposé de limite bloquante, pour une raison pragmatique : le nombre de
commandes simultanées d'un magasin est piloté par la trésorerie et les délais
fournisseurs, pas par une capacité de traitement humaine qu'on saurait chiffrer a priori.
Une limite arbitraire aurait été rejetée par les utilisateurs.

En revanche, le mécanisme dont la limite est le moyen — révéler l'engorgement — a bien
été implémenté, sous une autre forme. Chaque carte affiche depuis combien de jours elle
stagne dans sa colonne actuelle, et, lorsque la commande est confirmée, le nombre de
jours restants avant la livraison prévue. Le vieillissement des cartes est ainsi rendu
visible : une colonne « Soumis » remplie de cartes vieilles de dix jours raconte
exactement la même chose qu'une limite de travail en cours dépassée — le fournisseur ne
répond pas, le flux est bloqué en amont — sans avoir eu besoin d'interdire quoi que ce
soit. C'est une adaptation consciente de la méthode au contexte, pas un oubli.

### 3.5 Gérer le flux en temps réel

Un tableau Kanban vit de sa fraîcheur. S'il faut recharger la page pour voir qu'une
commande a bougé, il redevient un rapport, et un rapport ne coordonne personne.

Le tableau est donc connecté au serveur par une liaison temps réel permanente. Quand
l'agent de décision crée une suggestion depuis son fil d'exécution de fond, l'événement
est publié vers tous les navigateurs ouverts sur le magasin concerné, et la carte
apparaît sans intervention. Quand un utilisateur déplace une carte, les autres écrans
la voient bouger. Le tableau devient le lieu où l'humain et l'agent se coordonnent, en
direct, sur le même objet.

### 3.6 Fermer la boucle : la réception écrit dans le stock

C'est la dernière colonne, et c'est aussi celle qui referme le circuit du système entier.

Faire passer une carte en « Reçu » ou « Reçu partiellement » ne se contente pas de
changer un statut d'affichage. La même transaction enregistre un mouvement de stock de
type « réception de bon de commande » et incrémente la quantité disponible du produit
dans le magasin. Autrement dit, la marchandise entre réellement dans le système
d'information au moment où l'utilisateur la déclare arrivée.

Sans cette écriture, tout le reste s'effondrait : l'agent de décision, qui lit les niveaux
de stock, aurait continué à voir un stock insuffisant et aurait re-suggéré le même
réapprovisionnement à chaque cycle, remplissant la colonne « Suggéré » de doublons et
détruisant la confiance dans le système. Le Kanban n'est donc pas une couche de
présentation posée sur les agents : c'est le mécanisme par lequel l'action humaine
retourne dans l'état du monde que les agents observent.

### 3.7 Fermer une seconde boucle : l'apprentissage des agents

Chaque approbation et chaque rejet d'une suggestion est également journalisé comme un
retour humain sur une décision d'agent, avec le motif éventuel, l'urgence, la quantité,
et l'identité du décideur. Ces retours sont agrégés et réinjectés dans les instructions
données à l'agent de décision et à l'agent stratège lors des cycles suivants.

Le Kanban devient alors un instrument de mesure de la qualité des agents. Un taux de
rejet élevé sur les suggestions d'un certain type est un signal exploitable — le modèle
sur-commande, ou son seuil d'urgence est mal calibré. Cette journalisation est
délibérément non bloquante : si elle échoue, la transition du bon de commande est déjà
validée. On ne sacrifie jamais une opération métier à une opération d'observabilité.

### 3.8 Exposer le tableau aux agents eux-mêmes

Le tableau est enfin exposé comme un ensemble d'outils utilisables par les agents, via
le serveur MCP maison du module inventaire : lister les bons de commande d'un magasin,
consulter le détail de l'un d'eux, créer une suggestion, déplacer une carte dans une
colonne autorisée.

Deux décisions méritent d'être rappelées ici. La première est le refus d'un serveur MCP
Kanban générique existant, qui aurait stocké ses cartes dans sa propre base : le tableau
aurait alors décrit un flux imaginaire, déconnecté des bons de commande réels. Le choix
a été de garder PostgreSQL comme source de vérité unique et de faire des outils MCP de
simples enveloppes autour des règles métier déjà écrites — transitions autorisées,
quantités minimales de commande, sourcing fournisseur, clôture du stock à la réception.
La seconde est que ces outils exposent le déplacement, mais jamais l'approbation ni le
rejet. Un agent peut faire avancer une commande dans le flux logistique ; il ne peut pas
franchir la porte humaine. La règle du HITL survit au changement de canal d'accès.

L'agent analyste des ventes consulte ce tableau pour nuancer ses propres conclusions :
une rupture de stock dont le réassort est déjà en cours ne mérite pas la même alerte
qu'une rupture sur un produit que personne n'a commandé.

---

## 4. Ce que le Kanban a effectivement changé

Le tableau a transformé une chaîne de calcul en un système de travail.

Avant, la sortie des agents était une recommandation dans une table, consultée par une
page, et manuellement retranscrite dans un bon de commande — quand elle l'était. Le
chemin entre l'intelligence du système et l'acte d'achat reposait entièrement sur la
discipline d'un opérateur.

Après, la sortie des agents est une carte qui apparaît en temps réel sur un tableau,
qu'un humain approuve ou rejette d'un geste explicite, qui traverse ensuite un flux dont
les règles sont garanties par le système, et dont l'aboutissement met à jour le stock
que les agents observent, tout en renseignant les agents sur la qualité de leurs propres
propositions.

Le Kanban est le point de rencontre entre trois choses qui, sans lui, seraient restées
séparées : une décision de machine, un engagement d'humain, et un état du monde.

---

## 5. Limites assumées et pistes ouvertes

Ce document serait malhonnête s'il ne disait pas ce qui n'a pas été fait.

**Pas de limites de travail en cours chiffrées.** Le vieillissement des cartes remplit
la fonction d'alerte, mais aucune colonne ne refuse une carte supplémentaire. Si le
volume de commandes augmente fortement, il faudra probablement introduire des limites
réelles, au moins sur les colonnes d'attente fournisseur.

**Pas de métriques de flux.** Le temps de cycle — combien de jours s'écoulent entre la
suggestion et la réception — et le débit ne sont pas encore calculés ni affichés. Ce
sont pourtant les deux mesures qui donnent au Kanban sa valeur d'amélioration continue.
Les données existent en base pour les produire ; l'agrégation reste à écrire.

**Le temps réel ne couvre pas le canal MCP.** Le serveur MCP est un processus séparé de
l'API. Lorsqu'un agent déplace une carte par ce chemin, l'événement de diffusion, qui
vit en mémoire dans le processus de l'API, n'est pas émis : le tableau se met à jour au
rafraîchissement suivant. Faire transiter ce bus par Redis résoudrait le problème.

**La saisie de la quantité réellement reçue** en cas de réception partielle n'a pas
d'interface dédiée, et la sélection automatique du fournisseur le plus adapté n'est pas
implémentée.

Aucune de ces limites ne remet en cause la structure du tableau. Elles décrivent un
Kanban fonctionnel qu'il reste à instrumenter.
