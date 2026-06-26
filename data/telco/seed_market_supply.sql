SET client_encoding TO 'UTF8';

-- competitors
INSERT INTO market.competitors
    (concurrent_id, nom, code_operateur, pays, part_marche_pct, nb_abonnes, positionnement,
     points_forts, points_faibles, date_entree_marche, actif)
VALUES
('TT','Tunisie Telecom','TT','Tunisia',45.2,8200000,'STATE',
 '["couverture nationale","prix bas prepaye","reseau fixe"]',
 '["digital UX faible","innovation services"]','1996-01-01',TRUE),
('ORANGE','Orange Tunisie','OT','Tunisia',21.5,3900000,'PREMIUM',
 '["marque internationale","premium 4G/5G","service client"]',
 '["prix eleves","couverture rural"]','2010-07-01',TRUE),
('FREE_TN','Free Tunisie','FT','Tunisia',0.5,90000,'LOW_COST',
 '["prix tres bas","agressivite tarifaire"]',
 '["reseau limite","distribution faible"]','2023-01-01',TRUE)
ON CONFLICT (concurrent_id) DO UPDATE
    SET part_marche_pct=EXCLUDED.part_marche_pct, updated_at=NOW();

-- competitor pricing
INSERT INTO market.competitor_pricing
    (concurrent_id, categorie, produit_type, donnees_go, prix_ttc, engagement_mois, date_releve, source)
VALUES
('TT','FORFAIT_PREPAYE','Forfait 20Go Mensuel',20,17.0,0,'2026-01-15','WEB'),
('TT','FORFAIT_PREPAYE','Forfait 50Go Mensuel',50,30.0,0,'2026-01-15','WEB'),
('TT','FORFAIT_PREPAYE','Forfait 100Go Mensuel',100,50.0,0,'2026-01-15','WEB'),
('TT','FORFAIT_POSTPAYE','Forfait Illimite Starter',25,35.0,12,'2026-01-15','WEB'),
('TT','FORFAIT_POSTPAYE','Forfait Illimite Standard',60,60.0,12,'2026-01-15','WEB'),
('TT','FORFAIT_POSTPAYE','Forfait Illimite Premium',150,100.0,12,'2026-01-15','WEB'),
('TT','BOX_4G','Box 4G 100Go Mensuel',100,55.0,0,'2026-01-15','WEB'),
('TT','BOX_4G','Box 4G Illimitee',999,91.0,0,'2026-01-15','WEB'),
('TT','TERMINAL','Samsung Galaxy A16 4+128Go',NULL,599.0,0,'2026-01-15','VISITE_BOUTIQUE'),
('TT','TERMINAL','Samsung Galaxy A26 5G 8+128Go',NULL,1099.0,0,'2026-01-15','VISITE_BOUTIQUE'),
('ORANGE','FORFAIT_PREPAYE','Pack Data 20Go',20,19.0,0,'2026-01-15','WEB'),
('ORANGE','FORFAIT_PREPAYE','Pack Data 50Go',50,35.0,0,'2026-01-15','WEB'),
('ORANGE','FORFAIT_PREPAYE','Pack Data 100Go',100,60.0,0,'2026-01-15','WEB'),
('ORANGE','FORFAIT_POSTPAYE','Orange Go 30Go',30,40.0,12,'2026-01-15','WEB'),
('ORANGE','FORFAIT_POSTPAYE','Orange Go 60Go',60,70.0,12,'2026-01-15','WEB'),
('ORANGE','FORFAIT_POSTPAYE','Orange Go 150Go',150,110.0,12,'2026-01-15','WEB'),
('ORANGE','BOX_4G','Orange Box 100Go',100,60.0,0,'2026-01-15','WEB'),
('ORANGE','BOX_4G','Orange Box Illimitee',999,100.0,0,'2026-01-15','WEB'),
('ORANGE','TERMINAL','Samsung Galaxy A16 4+128Go',NULL,639.9,0,'2026-01-15','VISITE_BOUTIQUE'),
('ORANGE','TERMINAL','iPhone 15 128Go',NULL,1999.0,0,'2026-01-15','WEB')
ON CONFLICT DO NOTHING;

-- suppliers
INSERT INTO supply.suppliers
    (supplier_id, nom, pays_origine, type_fournisseur, categories, marques,
     delai_livraison_moy, delai_livraison_std, taux_fiabilite,
     commande_min, commande_multiple, devise, actif)
VALUES
('SAMSUNG_LEVANT','Samsung Electronics Levant','UAE','CONSTRUCTEUR',
 '["TERMINAL","ACCESSOIRE"]','["Samsung"]',14,3,0.93,20,10,'USD',TRUE),
('APPLE_MEA','Apple Middle East Africa','UAE','CONSTRUCTEUR',
 '["TERMINAL","ACCESSOIRE"]','["Apple"]',21,5,0.85,5,5,'USD',TRUE),
('TRANSSION_AFRICA','Transsion Holdings Africa','UAE','CONSTRUCTEUR',
 '["TERMINAL"]','["Tecno","Infinix","Itel"]',12,2,0.95,50,25,'USD',TRUE),
('XIAOMI_MEA','Xiaomi Middle East Africa','UAE','CONSTRUCTEUR',
 '["TERMINAL","ACCESSOIRE"]','["Xiaomi","Redmi","POCO"]',18,4,0.90,30,10,'USD',TRUE),
('NOKIA_HMD','HMD Global Nokia','Finland','CONSTRUCTEUR',
 '["TERMINAL"]','["Nokia"]',10,2,0.97,100,50,'EUR',TRUE),
('MBTECH_TN','MB Tech Tunisie','Tunisia','DISTRIBUTEUR_NATIONAL',
 '["TERMINAL","ACCESSOIRE"]','["Samsung","Apple","Xiaomi"]',5,1,0.96,10,5,'TND',TRUE),
('ABC_TN','ABC Technology Tunisie','Tunisia','DISTRIBUTEUR_NATIONAL',
 '["TERMINAL","ACCESSOIRE"]','["Nokia","Huawei","Infinix"]',4,1,0.94,10,5,'TND',TRUE),
('GEMALTO_SIM','Thales Gemalto SIM Cards','France','SIM_MANUFACTURER',
 '["SIM"]','["Ooredoo SIM"]',5,1,0.99,1000,1000,'EUR',TRUE),
('OOREDOO_CENTRAL','Entrepot Central Ooredoo TN','Tunisia','OPERATEUR_INTERNE',
 '["TERMINAL","SIM","ACCESSOIRE","RECHARGE"]','["ALL"]',2,1,0.99,1,1,'TND',TRUE),
('ORAIMO_DIST','Oraimo Distributeur TN','Tunisia','DISTRIBUTEUR_NATIONAL',
 '["ACCESSOIRE"]','["Oraimo"]',6,2,0.89,20,10,'TND',TRUE)
ON CONFLICT (supplier_id) DO NOTHING;

-- market events (key ones)
INSERT INTO market.events
    (event_name, event_type, sous_type, start_date, end_date, scope,
     uplift_terminal, uplift_forfait, uplift_sim, uplift_recharge, uplift_accessoire,
     intensite, note_strategie)
VALUES
('Ramadan 2024','RELIGIEUX','RAMADAN','2024-03-11','2024-04-09','NATIONAL',
 -15,25,10,60,5,'HIGH','Mois sacre: baisse achat terminaux, forte demande recharges et forfaits data'),
('Eid Al-Fitr 2024','RELIGIEUX','EID_FITR','2024-04-10','2024-04-12','NATIONAL',
 80,40,30,150,60,'EXTREME','Pic maximal annuel - cadeaux smartphones, SIM voyageurs, recharges massives'),
('Eid Al-Adha 2024','RELIGIEUX','EID_ADHA','2024-06-16','2024-06-18','NATIONAL',
 45,25,20,80,30,'HIGH','2e pic religieux - achats terminaux en famille'),
('Rentree Scolaire 2024','SCOLAIRE','RENTREE_SCOLAIRE','2024-09-01','2024-09-20','NATIONAL',
 55,35,40,20,25,'HIGH','Tablettes, smartphones enfants/etudiants, forfaits data'),
('Soldes Hiver 2024','COMMERCIAL','SOLDES','2024-11-25','2025-01-05','NATIONAL',
 65,20,15,15,35,'HIGH','Black Friday + soldes. Competition Orange/TT aggressive'),
('Noël 2024','COMMERCIAL','FETES_FIN_ANNEE','2024-12-24','2024-12-31','NATIONAL',
 50,20,10,30,40,'HIGH','Ventes terminaux haut de gamme, accessoires cadeaux'),
('Yennayer 2025','NATIONAL','YENNAYER','2025-01-12','2025-01-14','NATIONAL',
 20,10,10,25,15,'MEDIUM','Nouvel An berbere - pics regions interieures'),
('CAN 2025','SPORTIF','COUPE_AFRIQUE','2025-01-21','2025-02-18','NATIONAL',
 10,35,15,30,10,'HIGH','Forte demande forfaits streaming, Box 4G - pics matchs Tunisie'),
('Ramadan 2025','RELIGIEUX','RAMADAN','2025-03-01','2025-03-29','NATIONAL',
 -10,30,15,65,8,'HIGH','Pattern Ramadan 2024 + acceleration offres 5G'),
('Eid Al-Fitr 2025','RELIGIEUX','EID_FITR','2025-03-30','2025-04-01','NATIONAL',
 85,45,35,160,65,'EXTREME','Plus gros pic ventes annuel - preparer stocks 6 semaines avant'),
('Fete Independance 2025','NATIONAL','FETE_NATIONALE','2025-03-20','2025-03-20','NATIONAL',
 10,8,5,15,8,'LOW','Journee feriee courte'),
('Eid Al-Adha 2025','RELIGIEUX','EID_ADHA','2025-06-06','2025-06-08','NATIONAL',
 50,28,25,85,35,'HIGH','Cadeaux familiaux - hausse zones Centre et Sud'),
('Lancement 5G Ooredoo','CONCURRENTIEL','LANCEMENT_5G','2025-07-01','2025-07-31','NATIONAL',
 35,55,20,10,15,'HIGH','Lancement commercial 5G Ooredoo TN - forte demande forfaits 5G et routeurs'),
('Rentree Scolaire 2025','SCOLAIRE','RENTREE_SCOLAIRE','2025-09-02','2025-09-22','NATIONAL',
 60,40,45,22,28,'HIGH','Tablettes, smartphones etudiant, forfaits data illimite'),
('Mawlid 2025','RELIGIEUX','MAWLID','2025-09-04','2025-09-06','NATIONAL',
 20,15,10,35,15,'MEDIUM','Fete nationale religieuse'),
('Soldes Hiver 2025','COMMERCIAL','SOLDES','2025-11-28','2026-01-06','NATIONAL',
 70,22,18,18,40,'HIGH','Black Friday + soldes. Orange et TT agressifs sur prix terminaux'),
('Noel 2025','COMMERCIAL','FETES_FIN_ANNEE','2025-12-24','2025-12-31','NATIONAL',
 55,22,12,32,42,'HIGH','Pic terminaux premium - iPhone et Samsung Galaxy en tete'),
('Yennayer 2026','NATIONAL','YENNAYER','2026-01-12','2026-01-14','NATIONAL',
 22,12,12,28,17,'MEDIUM','Fete berbere - impact regional significatif'),
('Ramadan 2026','RELIGIEUX','RAMADAN','2026-02-17','2026-03-18','NATIONAL',
 -12,32,18,70,10,'HIGH','Ramadan hiver 2026 - soirees longues = plus activite digitale'),
('Eid Al-Fitr 2026','RELIGIEUX','EID_FITR','2026-03-20','2026-03-22','NATIONAL',
 90,48,38,170,70,'EXTREME','Eid 2026 - pic historique attendu avec 5G deployee'),
('Fete Independance 2026','NATIONAL','FETE_NATIONALE','2026-03-20','2026-03-20','NATIONAL',
 10,8,5,15,8,'LOW','Meme jour qu Eid 2026 cette annee - impact cumule fort'),
('Promo Orange Summer 2025','CONCURRENTIEL','PROMO_CONCURRENT','2025-07-15','2025-08-15','NATIONAL',
 -8,-12,-5,0,-5,'MEDIUM','Orange: smartphones -20% - surveiller pertes parts de marche'),
('Promo TT Rentree 2025','CONCURRENTIEL','PROMO_CONCURRENT','2025-08-25','2025-09-15','NATIONAL',
 -5,-8,-3,0,-3,'LOW','TT: offre rentree -15% sur forfaits etudiants'),
('Eid Al-Adha 2026','RELIGIEUX','EID_ADHA','2026-05-27','2026-05-29','NATIONAL',
 52,30,27,88,38,'HIGH','Eid Al-Adha 2026 prevu fin mai'),
('Soldes Ete 2025','COMMERCIAL','SOLDES_ETE','2025-07-01','2025-07-31','NATIONAL',
 30,15,10,10,20,'MEDIUM','Soldes ete: terminaux mid-range en promotion'),
('Soldes Ete 2026','COMMERCIAL','SOLDES_ETE','2026-07-01','2026-07-31','NATIONAL',
 32,16,11,11,22,'MEDIUM','Soldes 2026 avec 5G deployee - meilleure progression forfaits')
ON CONFLICT DO NOTHING;

-- MNP flows (sample)
INSERT INTO market.mnp_flows
    (direction, operateur_origine, operateur_destination, mois, volume, categorie_client, raison_principale, wilaya)
VALUES
('PORT_IN','TT','OOREDOO','2024-10-01',1245,'RESI','PRIX','Tunis'),
('PORT_IN','ORANGE','OOREDOO','2024-10-01',387,'RESI','PRIX','Tunis'),
('PORT_OUT','OOREDOO','TT','2024-10-01',543,'RESI','PRIX','Tunis'),
('PORT_OUT','OOREDOO','ORANGE','2024-10-01',289,'RESI','QUALITE_RESEAU','Tunis'),
('PORT_IN','TT','OOREDOO','2024-12-01',1856,'RESI','PROMO_FIN_ANNEE','Tunis'),
('PORT_IN','ORANGE','OOREDOO','2024-12-01',512,'RESI','PROMO_FIN_ANNEE','Tunis'),
('PORT_OUT','OOREDOO','TT','2024-12-01',478,'RESI','PRIX','Tunis'),
('PORT_IN','TT','OOREDOO','2025-03-01',2145,'RESI','RAMADAN','Tunis'),
('PORT_IN','ORANGE','OOREDOO','2025-03-01',687,'RESI','RAMADAN','Tunis'),
('PORT_OUT','OOREDOO','TT','2025-03-01',389,'RESI','PRIX','Tunis'),
('PORT_IN','TT','OOREDOO','2025-04-01',3456,'RESI','EID_FITR','Tunis'),
('PORT_IN','ORANGE','OOREDOO','2025-04-01',1123,'RESI','EID_FITR','Tunis'),
('PORT_OUT','OOREDOO','TT','2025-04-01',234,'RESI','PRIX','Tunis'),
('PORT_IN','TT','OOREDOO','2025-07-01',1890,'RESI','LANCEMENT_5G','Tunis'),
('PORT_OUT','OOREDOO','ORANGE','2025-07-01',345,'RESI','PROMO_ORANGE','Tunis'),
('PORT_IN','TT','OOREDOO','2025-09-01',2134,'RESI','RENTREE','Tunis'),
('PORT_OUT','OOREDOO','TT','2025-09-01',312,'RESI','PROMO_TT_RENTREE','Tunis'),
('PORT_IN','TT','OOREDOO','2025-12-01',2345,'RESI','SOLDES','Tunis'),
('PORT_IN','ORANGE','OOREDOO','2025-12-01',756,'RESI','SOLDES','Tunis'),
('PORT_IN','TT','OOREDOO','2026-03-01',3123,'RESI','RAMADAN_EID','Tunis'),
('PORT_IN','ORANGE','OOREDOO','2026-03-01',987,'RESI','RAMADAN_EID','Tunis'),
('PORT_OUT','OOREDOO','TT','2026-03-01',278,'RESI','PRIX','Tunis')
ON CONFLICT DO NOTHING;

-- seasonal patterns
INSERT INTO market.seasonal_patterns
    (categorie, mois, jour_semaine, facteur_demande, facteur_std, nb_annees_data, confidence, notes)
VALUES
('TERMINAL',1,5,1.45,0.18,3,'HIGH','Samedi janvier: soldes hiver actives'),
('TERMINAL',3,5,1.80,0.25,3,'VERY_HIGH','Samedi mars: Ramadan + fin mois = pic fort'),
('TERMINAL',4,5,2.50,0.30,3,'VERY_HIGH','Samedi Eid Al-Fitr = pic maximal annuel'),
('TERMINAL',4,6,2.30,0.28,3,'VERY_HIGH','Dimanche Eid: familles en boutique'),
('TERMINAL',6,5,1.55,0.20,3,'HIGH','Juin: Eid Al-Adha + soldes ete'),
('TERMINAL',7,5,1.40,0.18,3,'HIGH','Juillet: soldes ete, etudiants'),
('TERMINAL',9,5,1.65,0.22,3,'HIGH','Septembre: rentree scolaire = pic terminaux'),
('TERMINAL',11,5,1.70,0.22,3,'HIGH','Novembre: Black Friday debut'),
('TERMINAL',12,5,1.90,0.25,3,'VERY_HIGH','Decembre: soldes hiver + cadeaux'),
('FORFAIT',3,5,1.50,0.20,3,'VERY_HIGH','Mars Ramadan: streaming Iftar = forte demande data'),
('FORFAIT',3,6,1.60,0.22,3,'VERY_HIGH','Dimanche Ramadan: familles connectees'),
('FORFAIT',4,5,1.70,0.22,3,'VERY_HIGH','Avril: post-Eid, nouveaux contrats'),
('FORFAIT',7,5,1.45,0.20,3,'HIGH','Juillet: lancement 5G Ooredoo TN'),
('FORFAIT',9,5,1.50,0.20,3,'HIGH','Septembre: forfaits etudiants rentree'),
('FORFAIT',12,5,1.40,0.18,3,'HIGH','Decembre: cadeaux forfaits familles'),
('SIM',4,5,1.80,0.25,3,'VERY_HIGH','Eid Al-Fitr: cadeaux SIM, nouvelles lignes'),
('SIM',6,5,1.40,0.20,3,'HIGH','Juin: Eid Al-Adha, voyageurs ete'),
('SIM',9,5,1.55,0.22,3,'HIGH','Septembre: rentree etudiants, nouvelles SIM'),
('RECHARGE',3,5,2.20,0.30,3,'VERY_HIGH','Samedi Ramadan: recharges massives'),
('RECHARGE',4,5,2.50,0.35,3,'VERY_HIGH','Eid Al-Fitr: pic absolu recharges'),
('RECHARGE',6,5,1.60,0.20,3,'HIGH','Eid Al-Adha'),
('RECHARGE',12,5,1.50,0.20,3,'HIGH','Decembre: appels famille fin d annee'),
('ACCESSOIRE',4,5,1.90,0.28,3,'VERY_HIGH','Eid: housses, protections pour nouveaux smartphones'),
('ACCESSOIRE',9,5,1.60,0.22,3,'HIGH','Rentree: protections tablettes/smartphones etudiants'),
('ACCESSOIRE',12,5,1.70,0.24,3,'HIGH','Decembre: cadeaux accessoires'),
('ACCESSOIRE',11,5,1.50,0.20,3,'HIGH','Novembre: Black Friday accessoires')
ON CONFLICT (categorie, mois, COALESCE(jour_semaine, -1)) DO UPDATE
    SET facteur_demande=EXCLUDED.facteur_demande, updated_at=NOW();

SELECT 'events: ' || COUNT(*) FROM market.events;
SELECT 'seasonal_patterns: ' || COUNT(*) FROM market.seasonal_patterns;
SELECT 'mnp_flows: ' || COUNT(*) FROM market.mnp_flows;
SELECT 'competitors: ' || COUNT(*) FROM market.competitors;
SELECT 'competitor_pricing: ' || COUNT(*) FROM market.competitor_pricing;
SELECT 'suppliers: ' || COUNT(*) FROM supply.suppliers;

SELECT 'MARKET + SUPPLY SEED COMPLETE' AS status;
