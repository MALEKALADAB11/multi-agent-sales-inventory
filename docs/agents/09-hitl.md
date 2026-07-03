# HITL — Validation humaine (Human-in-the-Loop)

## Rôle

Le HITL n'est pas un agent au sens où les autres composants du système en sont : c'est le point de contrôle humain qui prend le relais lorsque l'Agent Guardrail juge qu'une décision ne peut raisonnablement pas être diffusée automatiquement, sans qu'elle soit pour autant fausse ou dangereuse au point d'être bloquée purement et simplement. Il matérialise le principe selon lequel certaines décisions à fort enjeu — financier ou commercial — doivent rester entre les mains d'un manager, même dans un système largement automatisé.

## Ce qui déclenche une escalade

Deux situations, détectées par l'Agent Guardrail, aboutissent systématiquement à une mise en attente de validation humaine. La première survient lorsque le score de confiance global d'une recommandation ou d'une réponse descend sous un seuil configuré : le système reconnaît alors explicitement qu'il n'est pas suffisamment certain de sa propre décision pour la diffuser sans un regard humain. La seconde survient lorsqu'une décision de réapprovisionnement implique un coût qui dépasse un plafond budgétaire configuré : au-delà de ce montant, la validation d'un manager est requise avant toute exécution, quelle que soit par ailleurs la qualité du raisonnement qui a mené à cette décision.

## Ce qu'il reçoit

Lorsqu'une escalade est déclenchée, le HITL reçoit la recommandation ou la décision concernée dans son intégralité, le motif précis de l'escalade tel qu'établi par le Guardrail, ainsi que l'identifiant du magasin et, le cas échéant, du conseiller concerné par la décision.

## Comment il fonctionne

La revue escaladée est stockée avec un statut initial "en attente". Un manager, depuis son propre espace de l'application, consulte la liste des revues en attente qui le concernent et statue sur chacune : il peut l'approuver, auquel cas la décision initialement proposée par le système est exécutée ou diffusée telle quelle, ou la rejeter, auquel cas elle n'a aucun effet. Chaque décision prise par le manager est conservée avec une trace complète : qui a statué, quand, et avec quel verdict, ce qui garantit une traçabilité totale des décisions à fort enjeu qui n'ont pas été laissées à la seule main du système automatisé.

## Ce qu'il produit

En sortie, le HITL fournit le statut final de chaque revue — en attente, approuvée ou rejetée — l'identité de la personne ayant statué, l'horodatage précis de sa décision, ainsi que des statistiques agrégées sur l'ensemble des revues : combien sont en attente, combien ont été approuvées, combien ont été rejetées. Ces statistiques sont exposées directement sur le tableau de bord de supervision, pour qu'un responsable puisse suivre en permanence la charge de validation humaine que le système lui soumet, et ajuster si besoin les seuils de confiance ou de budget qui déclenchent ces escalades.

## Pourquoi ce mécanisme compte

Un système entièrement automatisé qui ne prévoirait aucun point de contrôle humain pour ses décisions les plus incertaines ou les plus coûteuses prendrait un risque que ce projet a délibérément choisi d'éviter. Le HITL n'est pas un aveu de faiblesse du système : c'est au contraire la preuve que le système sait reconnaître les limites de sa propre confiance et les situations où l'enjeu financier justifie qu'un humain garde la main, plutôt que de prétendre à une automatisation totale et sans garde-fou.
