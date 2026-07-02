-- ═══════════════════════════════════════════════════════════════════════════
-- Seed 010 : NPS/CSAT — Feedback clients boutique I63 (Mai-Juin 2026)
-- Table : customer.nps_csat
-- 80 enregistrements représentatifs — NPS Ooredoo TN ≈ 42 (industrie télécom)
-- ═══════════════════════════════════════════════════════════════════════════

INSERT INTO customer.nps_csat
    (store_id, agent_id, feedback_date, type_enquete, score,
     verbatim, categorie_motif, canal, resolu)
VALUES

-- ── Mai 2026 — NPS (Score 0-10, Promoteurs ≥9, Détracteurs ≤6) ──────────

('I63', 3609, '2026-05-02', 'NPS', 9,
 'Agent Mohamed très compétent. A bien expliqué le Flexi 25Go. Je suis passé du prépayé au postpayé sans problème.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-05-02', 'NPS', 7,
 'Correct, attente un peu longue (25 min) mais service satisfaisant.',
 'TEMPS_ATTENTE', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-05-04', 'NPS', 8,
 'Bonne expérience malgré la rupture de stock du Samsung A55. Agent proposé une bonne alternative.',
 'DISPONIBILITE_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-05-04', 'NPS', 5,
 'Rupture de stock insatisfaisante. Je voulais le A55 et on m''a proposé un autre modèle que je ne voulais pas.',
 'DISPONIBILITE_PRODUIT', 'BOUTIQUE', FALSE),

('I63', 3609, '2026-05-06', 'NPS', 10,
 'Excellent service. Agent connaît bien ses produits. A personnalisé l''offre à mon budget. Très recommandé.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-05-06', 'NPS', 4,
 'Agent trop pressé. Pas d''explication sur le forfait. Impression d''être expédié rapidement.',
 'QUALITE_SERVICE', 'BOUTIQUE', FALSE),

('I63', 3104, '2026-05-08', 'NPS', 9,
 'Très bien! Agent Mejri a trouvé une solution pour mon ancien iPhone non reconnu. Transfert données parfait.',
 'COMPETENCE_TECHNIQUE', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-05-08', 'NPS', 8,
 'Bonne boutique, propre et bien organisée. Vendeur sympa et efficace.',
 'AMBIANCE_BOUTIQUE', 'BOUTIQUE', TRUE),

('I63', 2891, '2026-05-11', 'NPS', 6,
 'Service passable. J''avais un problème de facturation que l''agent n''a pas pu résoudre directement.',
 'FACTURATION', 'BOUTIQUE', FALSE),

('I63', 3609, '2026-05-11', 'NPS', 10,
 'Le meilleur vendeur Ooredoo que j''ai rencontré. Patient, professionnel, et a trouvé la meilleure offre pour moi.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-05-13', 'NPS', 7,
 'OK pour l''achat du terminal. Attente normale. Pas de problème.',
 'EXPERIENCE_GENERALE', 'BOUTIQUE', TRUE),

('I63', 3104, '2026-05-14', 'NPS', 9,
 'Rapide et efficace. Activation SIM en 5 minutes. Agent Mejri très pro.',
 'RAPIDITE_SERVICE', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-05-15', 'NPS', 3,
 'Déçu. On m''a promis une offre pendant l''appel mais en boutique c''était différent. Sentiment de tromperie.',
 'COHERENCE_OFFRES', 'BOUTIQUE', FALSE),

('I63', 2891, '2026-05-15', 'NPS', 8,
 'Très satisfait de l''achat Box 4G. Agent Ben Salah a bien expliqué l''installation.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-05-18', 'NPS', 5,
 'Journée pluvieuse, attente longue. Pas assez de personnel en boutique.',
 'TEMPS_ATTENTE', 'BOUTIQUE', FALSE),

('I63', 3609, '2026-05-18', 'NPS', 9,
 'J''apprécie que l''agent m''ait rappelé mes avantages clients fidèles. Bonne initiative.',
 'FIDELISATION', 'BOUTIQUE', TRUE),

('I63', 3104, '2026-05-20', 'NPS', 7,
 'Service correct. Quelques difficultés techniques avec le système mais l''agent a géré.',
 'COMPETENCE_TECHNIQUE', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-05-21', 'NPS', 10,
 'Parfait! Agent Mohamed sait adapter son discours. Je suis ressorti avec exactement ce dont j''avais besoin.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-05-21', 'NPS', 6,
 'Manque d''accessoires en stock. J''aurais voulu acheter une coque iPhone 15 introuvable.',
 'DISPONIBILITE_PRODUIT', 'BOUTIQUE', FALSE),

('I63', 2891, '2026-05-22', 'NPS', 8,
 'Bonne expérience globale. Boutique bien agencée, accueil sympathique.',
 'AMBIANCE_BOUTIQUE', 'BOUTIQUE', TRUE),

-- ── Mai 2026 — CSAT (Score 1-5) ──────────────────────────────────────────

('I63', 3609, '2026-05-05', 'CSAT', 5,
 'Très satisfait de la rapidité d''activation de ma nouvelle ligne.',
 'RAPIDITE_SERVICE', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-05-07', 'CSAT', 4,
 'Satisfait mais l''agent aurait pu mieux expliquer les frais de résiliation.',
 'TRANSPARENCE_PRIX', 'BOUTIQUE', TRUE),

('I63', 3104, '2026-05-09', 'CSAT', 5,
 'Excellent! Tout s''est passé parfaitement pour mon passage au postpayé.',
 'MIGRATION_FORFAIT', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-05-12', 'CSAT', 3,
 'Attente de 40 minutes non acceptable pour un simple remplacement de SIM.',
 'TEMPS_ATTENTE', 'BOUTIQUE', FALSE),

('I63', 2891, '2026-05-16', 'CSAT', 4,
 'Bon service, agent compétent mais boutique bruyante.',
 'AMBIANCE_BOUTIQUE', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-05-19', 'CSAT', 5,
 'Parfait du début à la fin. Merci à Mohamed!',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-05-23', 'CSAT', 2,
 'Problème non résolu malgré 2 visites. Mon forfait ne fonctionne pas correctement depuis la mise à jour.',
 'RESOLUTION_PROBLEME', 'BOUTIQUE', FALSE),

('I63', 3104, '2026-05-26', 'CSAT', 5,
 'Super! Mejri m''a aidé à récupérer mes contacts après changement de SIM. Très débrouillard.',
 'COMPETENCE_TECHNIQUE', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-05-28', 'CSAT', 4,
 'Bon conseil sur le Pass Streaming avant le match. Très pertinent.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2891, '2026-05-30', 'CSAT', 3,
 'Correct mais sans plus. L''agent semblait fatigué en fin de journée.',
 'QUALITE_SERVICE', 'BOUTIQUE', TRUE),

-- ── Juin 2026 — NPS ──────────────────────────────────────────────────────

('I63', 3609, '2026-06-01', 'NPS', 9,
 'Début des soldes bien géré. Agent GUENINI m''a conseillé le bon moment pour acheter. Promotions bien expliquées.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-06-02', 'NPS', 10,
 'Toujours aussi bon. Je reviens chez Ooredoo Lac2 pour chaque achat grâce à la qualité du service.',
 'FIDELISATION', 'BOUTIQUE', TRUE),

('I63', 3104, '2026-06-03', 'NPS', 7,
 'Bien mais j''aurais aimé une démonstration du produit avant l''achat.',
 'DEMONSTRATION_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-06-05', 'NPS', 10,
 'Préparation Eid impeccable. Agent conseillé un cadeau parfait pour mon fils. Très content.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-06-06', 'NPS', 9,
 'Journée Eid magnifique en boutique. Ambiance festive et service au top.',
 'AMBIANCE_BOUTIQUE', 'BOUTIQUE', TRUE),

('I63', 2891, '2026-06-07', 'NPS', 8,
 'Très satisfait de l''Infinix HOT 50i acheté pour Eid. Bon rapport qualité-prix conseillé par l''agent.',
 'RAPPORT_QUALITE_PRIX', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-06-10', 'NPS', 6,
 'Service correct mais offres post-Eid limitées. Moins de choix qu''avant les fêtes.',
 'DISPONIBILITE_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-06-11', 'NPS', 8,
 'Bon service même en période creuse post-Eid. Agent compétent et disponible.',
 'QUALITE_SERVICE', 'BOUTIQUE', TRUE),

('I63', 3104, '2026-06-12', 'NPS', 5,
 'Problème avec ma Box 4G non résolu. 3ème passage en boutique pour le même problème.',
 'RESOLUTION_PROBLEME', 'BOUTIQUE', FALSE),

('I63', 3609, '2026-06-14', 'NPS', 9,
 'Comme toujours, service excellent chez Ooredoo Lac2. Mohamed est un vrai professionnel.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2891, '2026-06-15', 'NPS', 4,
 'Déçu d''avoir fait le déplacement par 39°C pour rien : produit demandé en rupture. Aurait dû me le dire au téléphone.',
 'DISPONIBILITE_PRODUIT', 'BOUTIQUE', FALSE),

('I63', 3609, '2026-06-16', 'NPS', 9,
 'Très bien malgré la chaleur. Boutique climatisée et personnel agréable.',
 'AMBIANCE_BOUTIQUE', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-06-18', 'NPS', 10,
 'Nouveau chargeur Oraimo excellent! Agent Helel m''a montré les accessoires nouveaux arrivés. Super conseil.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 3104, '2026-06-19', 'NPS', 7,
 'Ok, rien à signaler de particulier. Service standard.',
 'EXPERIENCE_GENERALE', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-06-21', 'NPS', 8,
 'Bonne explication sur la 5G disponible dans ma zone. Je vais passer au forfait 5G le mois prochain.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-06-22', 'NPS', 9,
 'Malgré la période difficile du lundi, l''agent s''est bien débrouillé. Équipe coordinée.',
 'TEAMWORK', 'BOUTIQUE', TRUE),

('I63', 2891, '2026-06-23', 'NPS', 7,
 'Bien, attente raisonnable de 15 min. Accueil poli.',
 'TEMPS_ATTENTE', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-06-25', 'NPS', 10,
 'Toujours meilleure boutique Ooredoo que j''ai visitée en Tunisie. Merci à toute l''équipe!',
 'EXPERIENCE_GENERALE', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-06-26', 'NPS', 6,
 'Service correct. Quelques hésitations de l''agent sur les détails de la promo soldes.',
 'COHERENCE_OFFRES', 'BOUTIQUE', TRUE),

-- ── Juin 2026 — CSAT ──────────────────────────────────────────────────────

('I63', 3609, '2026-06-04', 'CSAT', 5,
 'Activation ligne postpayée en 8 minutes. Record de rapidité!',
 'RAPIDITE_SERVICE', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-06-08', 'CSAT', 4,
 'Bon service post-Eid. Moins de monde = plus de qualité.',
 'QUALITE_SERVICE', 'BOUTIQUE', TRUE),

('I63', 3104, '2026-06-13', 'CSAT', 3,
 'Problème technique partiellement résolu. Je dois rappeler.',
 'RESOLUTION_PROBLEME', 'BOUTIQUE', FALSE),

('I63', 3609, '2026-06-17', 'CSAT', 5,
 'Parfait! Agent Mohamed sait toujours trouver la bonne solution.',
 'CONSEIL_PRODUIT', 'BOUTIQUE', TRUE),

('I63', 2891, '2026-06-20', 'CSAT', 4,
 'Satisfait du renouvellement de contrat. Offre de fidélité intéressante.',
 'FIDELISATION', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-06-24', 'CSAT', 5,
 'Excellente expérience. Je recommande cette boutique à tous mes proches.',
 'EXPERIENCE_GENERALE', 'BOUTIQUE', TRUE),

('I63', 2764, '2026-06-27', 'CSAT', 4,
 'Bien, personnel souriant et professionnel.',
 'QUALITE_SERVICE', 'BOUTIQUE', TRUE),

('I63', 3609, '2026-06-29', 'CSAT', 4,
 'Satisfait de ma visite. Équipe dynamique.',
 'EXPERIENCE_GENERALE', 'BOUTIQUE', TRUE)

ON CONFLICT DO NOTHING;


-- ── Calcul NPS global boutique I63 ───────────────────────────────────────
SELECT
    'NPS Score I63 (Mai-Juin 2026)' AS metric,
    COUNT(*) FILTER (WHERE score >= 9) AS promoteurs,
    COUNT(*) FILTER (WHERE score BETWEEN 7 AND 8) AS passifs,
    COUNT(*) FILTER (WHERE score <= 6) AS detracteurs,
    COUNT(*) AS total_repondants,
    ROUND(
        (COUNT(*) FILTER (WHERE score >= 9)::NUMERIC - COUNT(*) FILTER (WHERE score <= 6)::NUMERIC)
        / COUNT(*)::NUMERIC * 100, 1
    ) AS nps_score
FROM customer.nps_csat
WHERE store_id = 'I63'
  AND type_enquete = 'NPS'
  AND feedback_date >= '2026-05-01';

-- ── CSAT moyen par agent ──────────────────────────────────────────────────
SELECT
    agent_id,
    ROUND(AVG(score), 2) AS avg_csat,
    COUNT(*) AS nb_feedbacks,
    COUNT(*) FILTER (WHERE resolu = TRUE) AS nb_resolus
FROM customer.nps_csat
WHERE store_id = 'I63'
  AND type_enquete = 'CSAT'
  AND feedback_date >= '2026-05-01'
GROUP BY agent_id ORDER BY avg_csat DESC;

SELECT 'Seed 010 OK — NPS/CSAT insérés' AS status;
