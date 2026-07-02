-- ═══════════════════════════════════════════════════════════════════════════
-- Seed 008 : Promotions actives Ooredoo Tunisie — Été 2026
-- Tables : inventory.promotions + market.events + market.seasonal_patterns
-- Schema réel : id(bigint auto), promo_id(text), promo_name, start_date,
--               end_date, sku(integer nullable), product_name, category,
--               discount_pct, promo_type, scope
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- 1. inventory.promotions — Offres en cours par catégorie ou SKU
-- ───────────────────────────────────────────────────────────────────────────

-- Purger nos promos seed identifiables avant ré-insertion
DELETE FROM inventory.promotions
WHERE promo_id LIKE 'SEED-2026-%';

INSERT INTO inventory.promotions
    (promo_id, promo_name, start_date, end_date,
     sku, product_name, category, discount_pct, promo_type, scope)
VALUES

-- ── Promo Été : -10% sur tous les terminaux (catégorie 50) ──────────────
('SEED-2026-T01', 'Soldes Été 2026 — Terminaux Samsung -10%',
 '2026-06-01', '2026-07-31',
 NULL, 'Samsung Galaxy (tous modèles)', '50', 10.0, 'discount', 'category'),

('SEED-2026-T02', 'Soldes Été 2026 — Infinix / Tecno -8%',
 '2026-06-01', '2026-07-31',
 NULL, 'Infinix / Tecno (tous modèles)', '50', 8.0, 'discount', 'category'),

-- ── Promo Forfaits : Upgrade Data ×2 pendant 3 mois ────────────────────
('SEED-2026-F01', 'Flexi+ 5G — Data ×2 pendant 3 mois',
 '2026-06-01', '2026-07-31',
 NULL, 'Forfait Flexi 5G', '88', 15.0, 'bundle', 'category'),

('SEED-2026-F02', 'Promo Flexi 25Go → 50Go même prix',
 '2026-06-01', '2026-07-31',
 NULL, 'Forfait Flexi 25Go', '88', 15.0, 'bundle', 'category'),

-- ── Remise Box 4G ────────────────────────────────────────────────────────
('SEED-2026-B01', 'Box 4G Illimitée -20% nouveaux abonnés',
 '2026-05-15', '2026-07-15',
 NULL, 'Box 4G Illimitée', '80', 20.0, 'discount', 'category'),

-- ── Bundle Accessoire offert ──────────────────────────────────────────────
('SEED-2026-A01', 'Bundle Accessoire Oraimo offert (achat terminal)',
 '2026-06-15', '2026-07-15',
 NULL, 'Accessoire Oraimo (chargeur ou écouteurs)', '70', 100.0, 'bundle', 'category'),

-- ── Activation SIM 4G gratuite ───────────────────────────────────────────
('SEED-2026-S01', 'Activation SIM 4G frais offerts',
 '2026-06-01', '2026-08-31',
 NULL, 'SIM Kit Ooredoo 4G', '20', 100.0, 'discount', 'category'),

-- ── Flash Sale week-end iPhone ────────────────────────────────────────────
('SEED-2026-I01', 'Flash Sale iPhone -5% (week-end)',
 '2026-06-28', '2026-07-06',
 NULL, 'iPhone 16 / 15 Pro', '50', 5.0, 'flash', 'category'),

-- ── Promo Back to School anticipée ───────────────────────────────────────
('SEED-2026-R01', 'Rentrée anticipée : forfait étudiant -15%',
 '2026-08-20', '2026-09-15',
 NULL, 'Forfait étudiant Ooredoo', '88', 15.0, 'seasonal', 'category'),

-- ── Eid Al-Adha : recharges doubles ──────────────────────────────────────
('SEED-2026-E01', 'Eid Al-Adha — Recharge ×2 crédit',
 '2026-06-04', '2026-06-08',
 NULL, 'Recharge tous montants', '30', 50.0, 'bundle', 'category'),

-- ── Pack Postpayé Premium été ─────────────────────────────────────────────
('SEED-2026-P01', 'Pack Postpayé été — 2 mois offerts',
 '2026-06-15', '2026-08-15',
 NULL, 'Forfait Postpayé Essentiel', '88', 17.0, 'seasonal', 'category'),

-- ── Accessoires -25% Soldes ──────────────────────────────────────────────
('SEED-2026-A02', 'Soldes accessoires -25%',
 '2026-07-01', '2026-07-31',
 NULL, 'Accessoires Oraimo (tous)', '70', 25.0, 'seasonal', 'category')

ON CONFLICT (promo_id) DO UPDATE SET
    end_date     = EXCLUDED.end_date,
    discount_pct = EXCLUDED.discount_pct;


-- ───────────────────────────────────────────────────────────────────────────
-- 2. market.events — Événements commerciaux été 2026
-- ───────────────────────────────────────────────────────────────────────────

INSERT INTO market.events
    (event_name, event_type, sous_type, start_date, end_date, scope,
     uplift_terminal, uplift_forfait, uplift_sim, uplift_recharge, uplift_accessoire,
     intensite, note_strategie)
VALUES

('Soldes Été 2026', 'COMMERCIAL', 'SOLDES',
 '2026-06-15', '2026-08-15', 'NATIONAL',
 25, 10, 8, 5, 20, 'HIGH',
 'Terminaux + accessoires. Pic week-end début période. Bundle accessoire recommandé.'),

('Fête de la Jeunesse 2026', 'NATIONAL', 'FETE_NATIONALE',
 '2026-08-13', '2026-08-13', 'NATIONAL',
 12, 8, 5, 10, 8, 'MEDIUM',
 'Journée fériée. Forte affluence milieu matinée. Cibler segment ETUDIANT.'),

('Rentrée Scolaire 2026', 'SCOLAIRE', 'RENTREE_SCOLAIRE',
 '2026-09-01', '2026-09-20', 'NATIONAL',
 55, 35, 40, 20, 25, 'HIGH',
 'Tablettes, smartphones étudiants, forfaits data. Préparer stock dès fin août.'),

('Promo Back to School Ooredoo 2026', 'CONCURRENTIEL', 'PROMO_OPERATEUR',
 '2026-08-20', '2026-09-15', 'NATIONAL',
 30, 25, 30, 15, 20, 'HIGH',
 'Bundle smartphone étudiant + forfait 50Go à 89 TND. Différenciant vs TT et Orange.'),

('Fête de la Femme 2026', 'NATIONAL', 'FETE_NATIONALE',
 '2026-08-13', '2026-08-13', 'NATIONAL',
 18, 12, 8, 12, 22, 'MEDIUM',
 'Cadeaux smartwatch, accessoires. Upsell montre + accessoire. Cibler segment féminin.'),

('Congestion réseau côtier Été 2026', 'METEO', 'RESEAU',
 '2026-07-15', '2026-08-15', 'NATIONAL',
 -5, -8, -3, 5, -2, 'LOW',
 'Saturation réseau zones touristiques. Opportunité Box 4G résidences.'),

('Eid Al-Adha 2026', 'RELIGIEUX', 'EID_ADHA',
 '2026-06-06', '2026-06-08', 'NATIONAL',
 50, 28, 25, 85, 35, 'HIGH',
 'Cadeaux familiaux. Hausse Centre/Sud. Terminaux entrée gamme + recharges.')

ON CONFLICT DO NOTHING;


-- ───────────────────────────────────────────────────────────────────────────
-- 3. market.seasonal_patterns — Été 2026 (juillet-septembre)
-- ───────────────────────────────────────────────────────────────────────────

INSERT INTO market.seasonal_patterns
    (categorie, mois, jour_semaine, facteur_demande, facteur_std,
     nb_annees_data, confidence, notes)
VALUES
-- Juillet
('TERMINAL', 7, 5, 1.40, 0.18, 3, 'HIGH', 'Samedi juillet : soldes été, étudiants'),
('TERMINAL', 7, 6, 1.25, 0.16, 3, 'HIGH', 'Dimanche juillet : familles'),
('TERMINAL', 7, 0, 0.92, 0.12, 3, 'MEDIUM', 'Lundi juillet : post week-end'),
('FORFAIT',  7, 5, 1.15, 0.14, 3, 'MEDIUM', 'Samedi juillet : offres vacances streaming'),
('FORFAIT',  7, 0, 0.88, 0.10, 3, 'MEDIUM', 'Lundi juillet : légère baisse'),
('RECHARGE', 7, 5, 1.30, 0.15, 3, 'HIGH',   'Samedi juillet : touristes et recharges'),
('ACCESSOIRE',7, 5, 1.35, 0.18, 3, 'HIGH',  'Samedi juillet : cadeaux + soldes'),

-- Août
('TERMINAL', 8, 6, 1.10, 0.15, 3, 'MEDIUM', 'Dimanche août : vacances'),
('TERMINAL', 8, 5, 1.20, 0.16, 3, 'MEDIUM', 'Samedi août : avant rentrée'),
('TERMINAL', 8, 0, 0.78, 0.12, 3, 'MEDIUM', 'Lundi août : creux estival'),
('FORFAIT',  8, 5, 1.10, 0.12, 3, 'MEDIUM', 'Samedi août : rentrée anticipée'),
('FORFAIT',  8, 0, 0.82, 0.10, 3, 'MEDIUM', 'Lundi août : creux'),
('SIM',      8, 5, 1.20, 0.14, 3, 'MEDIUM', 'Août : SIM touristes'),
('RECHARGE', 8, 5, 1.25, 0.14, 3, 'MEDIUM', 'Août : recharges touristes'),

-- Septembre
('TERMINAL', 9, 5, 1.65, 0.22, 3, 'HIGH',  'Samedi rentrée : pic terminaux'),
('TERMINAL', 9, 6, 1.50, 0.20, 3, 'HIGH',  'Dimanche rentrée : familles + enfants'),
('TERMINAL', 9, 0, 1.25, 0.16, 3, 'HIGH',  'Lundi rentrée : achats urgents'),
('FORFAIT',  9, 5, 1.45, 0.18, 3, 'HIGH',  'Samedi sept : forfaits étudiants'),
('FORFAIT',  9, 0, 1.20, 0.14, 3, 'HIGH',  'Lundi sept : activation forfaits rentrée'),
('SIM',      9, 5, 1.55, 0.20, 3, 'HIGH',  'Samedi sept : SIM étudiants nouveaux'),
('ACCESSOIRE',9, 5, 1.40, 0.18, 3, 'HIGH', 'Samedi sept : protections, chargeurs'),
('ACCESSOIRE',9, 6, 1.30, 0.16, 3, 'HIGH', 'Dimanche sept : accessoires rentrée')

ON CONFLICT (categorie, mois, COALESCE(jour_semaine, -1)) DO UPDATE
    SET facteur_demande = EXCLUDED.facteur_demande,
        facteur_std     = EXCLUDED.facteur_std,
        confidence      = EXCLUDED.confidence,
        notes           = EXCLUDED.notes;


SELECT 'Seed 008 OK' AS status;
SELECT COUNT(*) AS nb_promos FROM inventory.promotions WHERE promo_id LIKE 'SEED-2026-%';
SELECT COUNT(*) AS nb_events FROM market.events;
SELECT COUNT(*) AS nb_patterns FROM market.seasonal_patterns;
