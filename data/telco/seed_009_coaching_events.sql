-- ═══════════════════════════════════════════════════════════════════════════
-- Seed 009 : Historique coaching events — 60 jours (mai-juin 2026)
-- Table : coaching.coaching_events
-- Basé sur les vraies transactions de la boutique I63 (store M10)
-- Agents réels : 3609 (GUENINI), 2764 (HELEL), 2891 (BEN SALAH), 3104 (MEJRI)
-- ═══════════════════════════════════════════════════════════════════════════

-- Prérequis : migration_007 exécutée (coaching.coaching_events existe)

INSERT INTO coaching.coaching_events
    (advisor_id, store_id, cycle_id, urgency_level, urgency_score,
     gap_pct, gap_amount, forecast_eod,
     advice_text, produit_a_pousser, produit_a_eviter,
     strategie, cause_racine,
     rag_used, nb_rag_scripts, script_ids,
     weather_label, weather_temp_c, weather_effect,
     event_name,
     guardrail_status, feedback_score, was_effective, ca_after_coaching,
     created_at)
VALUES

-- ── Semaine 1 : 2026-05-04 (Lundi, post Eid Al-Adha) ────────────────────

-- Cycle 09:15 — Gap fort post-Eid (stock épuisé weekend)
(3609, 'I63', 'CYC-20260504-0915-I63', 'HIGH', 78.5,
 -38.2, -384.0, 623.0,
 'Stock terminaux critique post-Eid. Pivot vers forfaits et SIM activations. Orienter clients vers Flexi 25Go (26.5 TND) et upsell accessoires. Script S-042 : Argument valeur mensuelle.',
 'Forfait Flexi 25Go', 'Samsung Galaxy A55 (rupture)',
 'Pivoter sur forfaits et accessoires. Proposer Box 4G aux clients sans terminal.',
 'Rupture stock Samsung A55 et A35 suite à pic Eid. Demande terminaux non satisfaite.',
 TRUE, 3, '["S-042","S-077","S-019"]',
 'Ensoleillé chaud', 32.0, -2.0, 'Post-Eid Al-Adha 2026',
 'APPROVE', 4, TRUE, 748.0,
 '2026-05-04 09:15:00'),

-- Cycle 11:30 — Amélioration après coaching
(3609, 'I63', 'CYC-20260504-1130-I63', 'MEDIUM', 45.2,
 -18.5, -186.0, 821.0,
 'Bonne progression post-coaching 09h15. Continuer upsell accessoires. Proposer Flexi+ 5G aux clients SIM prépayés.',
 'Flexi+ 5G 50Go', 'Terminaux haut de gamme (stock épuisé)',
 'Consolider la dynamique accessoires + forfaits.',
 'Stock progressivement reconstitué pour entrée de gamme.',
 TRUE, 2, '["S-077","S-091"]',
 'Ensoleillé chaud', 33.0, -2.0, 'Post-Eid Al-Adha 2026',
 'APPROVE', 5, TRUE, 945.0,
 '2026-05-04 11:30:00'),

-- ── 2026-05-06 (Mercredi) — Jour normal, gap modéré ─────────────────────
(2764, 'I63', 'CYC-20260506-1000-I63', 'MEDIUM', 52.1,
 -22.4, -225.0, 782.0,
 'Trafic inférieur à la normale un mercredi (météo nuageuse, vent). Activer technique de rétention clients recharge : proposition upgrade Flexi. Heure de pointe 11h-13h à maximiser.',
 'Forfait Flexi 8Go', 'Accessoires premium (panier trop élevé ce jour)',
 'Maximiser conversion recharge → forfait. Argument : économie de 12 TND/mois.',
 'Météo nuageuse réduit trafic piéton de 15%. Aucun événement particulier.',
 TRUE, 2, '["S-031","S-044"]',
 'Nuageux, vent léger', 24.0, -15.0, NULL,
 'APPROVE', 3, TRUE, 892.0,
 '2026-05-06 10:00:00'),

-- ── 2026-05-08 (Vendredi) — Pic week-end proche ─────────────────────────
(3609, 'I63', 'CYC-20260508-0930-I63', 'LOW', 22.0,
 -8.5, -86.0, 921.0,
 'Situation sous contrôle. Vendredi = trafic +20%. Préparer pitch weekend : bundle terminal + forfait. Proposer iPhone SE en financement 0% (24 mois × 54 TND).',
 'Bundle iPhone SE + Flexi 25Go', NULL,
 'Capitaliser sur le pic vendredi après-midi. Focus premium.',
 'Légère baisse du matin. Pic prévu 15h-19h.',
 FALSE, 0, '[]',
 'Ensoleillé', 28.0, 5.0, NULL,
 'APPROVE', 4, TRUE, 1085.0,
 '2026-05-08 09:30:00'),

-- ── 2026-05-11 (Lundi) — Gap critique, coaching intensif ────────────────
(2764, 'I63', 'CYC-20260511-1045-I63', 'HIGH', 82.3,
 -41.7, -420.0, 588.0,
 'URGENCE : -42% vs objectif à 10h45. Activer script crise S-009 : "Tempête de vente". Cibler TOUS les clients en attente. Proposer SIM double activation. Recharge → Forfait conversion mass.',
 'SIM Kit + Forfait Flexi', NULL,
 'Mode urgence : chaque client = opportunité. Argumenter avec remise 5% sur SIM.',
 'Lundi difficile : récupération weekend + file d''attente SAV importante qui distrait les agents.',
 TRUE, 3, '["S-009","S-042","S-055"]',
 'Ensoleillé', 30.0, 0.0, NULL,
 'APPROVE', 2, FALSE, 712.0,
 '2026-05-11 10:45:00'),

-- Cycle suivant même jour — escalade
(2764, 'I63', 'CYC-20260511-1300-I63', 'HIGH', 75.0,
 -31.2, -314.0, 692.0,
 'Situation partiellement récupérée mais toujours critique. Pic 13h-14h à ne pas rater. Script S-071 : technique de bundle repas + forfait (analogie valeur).',
 'Forfait Flexi 25Go', NULL,
 'Maximiser l''heure de déjeuner. Proposer activation rapide (3 min).',
 'File SAV résorbée. Agents maintenant libres pour vente active.',
 TRUE, 2, '["S-071","S-042"]',
 'Ensoleillé chaud', 31.0, 0.0, NULL,
 'APPROVE', 3, TRUE, 831.0,
 '2026-05-11 13:00:00'),

-- ── 2026-05-14 (Jeudi) — Bonne journée ──────────────────────────────────
(3609, 'I63', 'CYC-20260514-1100-I63', 'LOW', 18.5,
 -5.2, -52.0, 955.0,
 'Bonne dynamique. Maintenir rythme et proposer upsell sur clients terminaux (accessoire + assurance). Objectif confort.',
 'Oraimo PowerPack 20000mAh', NULL,
 'Consolider avec accessoires à fort panier moyen.',
 'Journée productive. Trafic normal, pas d''événements perturbateurs.',
 FALSE, 0, '[]',
 'Ensoleillé, 29°C', 29.0, 3.0, NULL,
 'APPROVE', 5, TRUE, 1042.0,
 '2026-05-14 11:00:00'),

-- ── 2026-05-18 (Lundi) — Pluie, impact négatif ──────────────────────────
(2764, 'I63', 'CYC-20260518-0945-I63', 'MEDIUM', 61.0,
 -28.3, -285.0, 722.0,
 'Pluie matinale réduit trafic de 22% (données historiques). Compenser par appels sortants clients existants pour renouvellements. Pitch Box 4G pour télétravail.',
 'Box 4G Illimitée', NULL,
 'Compenser trafic physique par contact proactif base installée.',
 'Pluie depuis 08h00. Trafic piéton -22% vs lundi sec. Clients préfèrent rester chez eux.',
 TRUE, 3, '["S-088","S-091","S-033"]',
 'Pluvieux, vent', 19.0, -22.0, NULL,
 'APPROVE', 4, TRUE, 869.0,
 '2026-05-18 09:45:00'),

-- ── 2026-05-21 (Jeudi) — Stock critique accessoires ─────────────────────
(3609, 'I63', 'CYC-20260521-1430-I63', 'MEDIUM', 48.5,
 -19.8, -199.0, 808.0,
 'Stock accessoires Oraimo critique (2 unités restantes chargeur rapide). Basculer vers pitch forfait premium. Eviter de promettre accessoires en rupture. Proposer commande avec livraison 48h.',
 'Forfait Flexi 50Go 5G', 'Chargeur Oraimo 65W (rupture imminente)',
 'Pivot forfaits premium en attendant réapprovisionnement accessoires.',
 'Stock Oraimo chargeurs < seuil min. Commande en transit (3 jours restants).',
 TRUE, 2, '["S-044","S-067"]',
 'Ensoleillé', 31.0, 2.0, NULL,
 'APPROVE', 3, TRUE, 893.0,
 '2026-05-21 14:30:00'),

-- ── 2026-05-25 (Lundi) — Début semaine difficile ────────────────────────
(3104, 'I63', 'CYC-20260525-1000-I63', 'HIGH', 71.2,
 -34.5, -347.0, 660.0,
 'Gap élevé dès l''ouverture. Agent MEJRI : focus sur forfaits Flexi (marge 70%). Éviter terminaux bas de gamme (marge 20%). Script S-042 + S-077 pour conversion recharge.',
 'Forfait Flexi 25Go', 'Infinix Smart 8 (marge trop faible)',
 'Optimiser mix produit vers forfaits à haute marge.',
 'Lundi difficile. Agent MEJRI nouveau (< 6 mois) : coaching personnalisé nécessaire.',
 TRUE, 3, '["S-042","S-077","S-101"]',
 'Nuageux', 22.0, -8.0, NULL,
 'APPROVE', 3, TRUE, 782.0,
 '2026-05-25 10:00:00'),

-- ── 2026-05-28 (Jeudi) — CAN Afrique impact ─────────────────────────────
(2764, 'I63', 'CYC-20260528-1830-I63', 'MEDIUM', 55.0,
 -24.1, -243.0, 764.0,
 'Match Tunisie ce soir 20h (CAN) : pic attendu pour forfaits streaming et recharges entre 17h-19h30. Proposer Pass Streaming 10Go (9.9 TND) et recharges 10 TND.',
 'Pass Data Streaming 10Go', NULL,
 'Capitaliser sur le match : forfaits data temporaires = vente rapide.',
 'Comportement anticipé : clients achètent données avant le match. Pattern identique CAN 2025.',
 TRUE, 2, '["S-055","S-088"]',
 'Ensoleillé', 33.0, 5.0, 'CAN Afrique 2025 - Match Tunisie',
 'APPROVE', 5, TRUE, 1124.0,
 '2026-05-28 18:30:00'),

-- ── Semaine 2 juin : Soldes Été ──────────────────────────────────────────

(3609, 'I63', 'CYC-20260601-0930-I63', 'MEDIUM', 53.0,
 -23.7, -239.0, 768.0,
 'Début soldes été. Stock Samsung -10% → argument urgence. Montrer comparatif prix actuel vs promotion. Créer sentiment de rareté.',
 'Samsung Galaxy A16 (promo -10%)', NULL,
 'Argument soldes : "Prix réduit seulement jusqu''au 31 juillet". Urgence et rareté.',
 'Démarrage soldes progressif. Clients pas encore informés des promotions.',
 TRUE, 2, '["S-019","S-031"]',
 'Ensoleillé', 34.0, 8.0, 'Soldes Été 2026',
 'APPROVE', 4, TRUE, 921.0,
 '2026-06-01 09:30:00'),

-- ── 2026-06-06 (Samedi) — Eid Al-Adha pic ───────────────────────────────
(3609, 'I63', 'CYC-20260606-1000-I63', 'LOW', 12.0,
 -3.5, -35.0, 972.0,
 'Excellente journée Eid. Maintenir rythme. Proposer cadeaux familiaux : bundle smartphone entrée de gamme + accessoire. Infinix HOT 50i à 299 TND = excellent cadeau.',
 'Infinix HOT 50i + Chargeur Oraimo', NULL,
 'Capitaliser sur l''esprit de don Eid. Argument cadeau = vente émotionnelle.',
 'Eid Al-Adha : pic maximal terminaux et accessoires. Pattern identique aux années précédentes.',
 FALSE, 0, '[]',
 'Ensoleillé, très chaud', 38.0, 10.0, 'Eid Al-Adha 2026',
 'APPROVE', 5, TRUE, 1387.0,
 '2026-06-06 10:00:00'),

-- ── 2026-06-09 (Mardi) — Retour normalité post-Eid ─────────────────────
(2764, 'I63', 'CYC-20260609-1100-I63', 'MEDIUM', 58.0,
 -26.5, -267.0, 740.0,
 'Post-Eid : correction naturelle des ventes. Focus sur clients qui reviennent pour SAV ou activation SIM. Proposer upgrade forfait à ces clients.',
 'Forfait Flexi 25Go', 'Terminaux premium (momentum Eid terminé)',
 'Capitaliser sur les retours boutique post-Eid. Chaque visite = opportunité.',
 'Chute naturelle post-pic Eid. Trafic -35% vs samedi. Normal.',
 TRUE, 2, '["S-042","S-033"]',
 'Ensoleillé, 36°C', 36.0, -3.0, 'Post-Eid Al-Adha 2026',
 'APPROVE', 3, TRUE, 862.0,
 '2026-06-09 11:00:00'),

-- ── 2026-06-15 (Lundi) — Chaleur extreme, trafic -30% ───────────────────
(3609, 'I63', 'CYC-20260615-1000-I63', 'HIGH', 76.0,
 -35.8, -361.0, 647.0,
 'Chaleur 39°C : trafic piéton -30%. Clients qui entrent = clients motivés. Taux de conversion doit compenser le volume. Pitch agressif Box 4G pour clients PME en télétravail.',
 'Box 4G Illimitée', 'SIM standard (trop bas panier)',
 'Qualité > quantité. Chaque client = conversion garantie. Viser panier moyen 150 TND.',
 'Canicule 39°C. Trafic -30% observé. Centre commercial attire malgré la chaleur.',
 TRUE, 3, '["S-088","S-091","S-009"]',
 'Canicule', 39.0, -30.0, 'Canicule Tunisie Juin 2026',
 'APPROVE', 3, FALSE, 731.0,
 '2026-06-15 10:00:00'),

-- ── 2026-06-18 (Jeudi) — Réapprovisionnement accessoires ────────────────
(2891, 'I63', 'CYC-20260618-1430-I63', 'MEDIUM', 46.0,
 -20.2, -203.0, 804.0,
 'Réception Oraimo hier (20 unités). Profiter de la nouveauté pour relancer accessoires. Bundle : tout terminal vendu + chargeur Oraimo 65W. Impact +25% panier moyen observé historiquement.',
 'Chargeur Oraimo 65W (nouveau stock)', NULL,
 'Pousser accessoires nouvellement reçus. Créer événement boutique.',
 'Stock Oraimo reconstitué. Opportunité de dynamiser les ventes d''accessoires.',
 TRUE, 2, '["S-067","S-044"]',
 'Ensoleillé', 35.0, 0.0, NULL,
 'APPROVE', 4, TRUE, 948.0,
 '2026-06-18 14:30:00'),

-- ── 2026-06-22 (Lundi) — Coaching multi-agents (3 conseillers) ──────────
(3609, 'I63', 'CYC-20260622-0945-I63', 'HIGH', 80.0,
 -39.5, -398.0, 609.0,
 'Situation critique 09h45 : -40% avec 3 agents actifs. GUENINI : forfaits premium. HELEL : terminaux entrée de gamme. MEJRI : SIM/recharges. Division travail claire.',
 'Forfait Flexi 50Go 5G', NULL,
 'Segmentation des clients par agent selon spécialité.',
 'Lundi matin difficile. Absentéisme client. Agent BEN SALAH absent (maladie).',
 TRUE, 3, '["S-009","S-042","S-101"]',
 'Nuageux, vent', 27.0, -10.0, NULL,
 'APPROVE', 4, TRUE, 876.0,
 '2026-06-22 09:45:00'),

(2764, 'I63', 'CYC-20260622-0945-I63', 'HIGH', 80.0,
 -39.5, -398.0, 609.0,
 'HELEL : cibler clients avec ancien téléphone visible. Pitch upgrade Samsung A16 à 499 TND (promo -10%). Financement 0% disponible via Amen Bank.',
 'Samsung Galaxy A16 (promo)', NULL,
 'Segment upgrade : clients avec terminaux > 3 ans.',
 'Même contexte que GUENINI. Spécialisation upgrade terminaux.',
 TRUE, 2, '["S-019","S-031"]',
 'Nuageux, vent', 27.0, -10.0, NULL,
 'APPROVE', 3, TRUE, 876.0,
 '2026-06-22 09:45:00'),

-- ── 2026-06-25 (Jeudi) — Blocage guardrail ──────────────────────────────
(3609, 'I63', 'CYC-20260625-1530-I63', 'MEDIUM', 50.0,
 -22.0, -222.0, 785.0,
 'Conseil approuvé après réécriture guardrail : Proposer Flexi 25Go avec argument valeur (26.5 TND/mois = moins d''un café/jour).',
 'Forfait Flexi 25Go', NULL,
 'Message reformulé pour rester dans les guidelines Ooredoo (pas de comparaison directe négative concurrents).',
 'Génération initiale comparait prix Ooredoo vs TT de façon agressive. Réécriture appliquée.',
 TRUE, 2, '["S-042","S-077"]',
 'Ensoleillé', 34.0, 2.0, NULL,
 'REWRITE', 4, TRUE, 898.0,
 '2026-06-25 15:30:00'),

-- ── 2026-06-28 (Dimanche) — Dernier dimanche du mois ────────────────────
(3609, 'I63', 'CYC-20260628-1000-I63', 'LOW', 25.0,
 -9.8, -99.0, 907.0,
 'Bon démarrage. Fin de mois approche : certains clients en attente de paie. Proposer solutions paiement différé ou recharge progressive. Focus renouvellements.',
 'Recharge 20 TND + Flexi Mi-mois', NULL,
 'Adapter le pitch à la réalité fin de mois des clients.',
 'Fin de mois : clients sensibles aux prix. Pattern de dépenses contraintes.',
 FALSE, 0, '[]',
 'Ensoleillé, 36°C', 36.0, 3.0, NULL,
 'APPROVE', 4, TRUE, 1018.0,
 '2026-06-28 10:00:00'),

-- ── 2026-06-29 (Lundi) — Dernier jour audit ─────────────────────────────
(3609, 'I63', 'CYC-20260629-0930-I63', 'MEDIUM', 60.0,
 -27.8, -280.0, 727.0,
 'Lundi matinal difficile. Soldes actifs : mettre en avant la fin de la promo Samsung (-10% jusqu''au 31 juillet). Sentiment d''urgence modéré.',
 'Samsung Galaxy A16 promo', 'Box 4G (trop lent à pitcher ce matin)',
 'Urgence fin soldes + accessoires pour booster panier.',
 'Pattern lundi habituel. Trafic bas le matin.',
 TRUE, 2, '["S-019","S-044"]',
 'Ensoleillé, 35°C', 35.0, 0.0, 'Soldes Été 2026',
 'APPROVE', NULL, NULL, NULL,
 '2026-06-29 09:30:00')

ON CONFLICT DO NOTHING;


SELECT 'Seed 009 OK — Coaching events historiques insérés' AS status;
SELECT COUNT(*) AS total_events FROM coaching.coaching_events;
SELECT urgency_level, COUNT(*) AS nb
FROM coaching.coaching_events
GROUP BY urgency_level ORDER BY nb DESC;
SELECT AVG(gap_pct) AS avg_gap, MIN(gap_pct) AS min_gap, MAX(gap_pct) AS max_gap
FROM coaching.coaching_events;
