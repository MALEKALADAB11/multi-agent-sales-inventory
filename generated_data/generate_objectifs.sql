-- Objectifs journaliers pour toutes les boutiques actives
BEGIN;
DELETE FROM sales.objectifs WHERE date_objectif >= CURRENT_DATE - INTERVAL '45 days';

INSERT INTO sales.objectifs (store_id, agent_id, date_objectif, objectif_ca, objectif_transactions, objectif_panier_moyen)
SELECT
    b.store_id,
    NULL,
    d::date,
    CASE
        WHEN b.store_id = 'I63' THEN 8500 + (random() * 1500)::int   -- Lac 2 flagship
        WHEN b.ville IN ('TUNIS', 'Tunis') THEN 6500 + (random() * 2000)::int
        WHEN b.ville IN ('SOUSSE', 'Sousse', 'SFAX', 'Sfax') THEN 4500 + (random() * 1500)::int
        ELSE 3000 + (random() * 1500)::int
    END,
    CASE
        WHEN b.ville IN ('TUNIS', 'Tunis') THEN 50 + (random() * 30)::int
        ELSE 30 + (random() * 20)::int
    END,
    120 + (random() * 60)::int
FROM sales.boutiques b
CROSS JOIN generate_series(CURRENT_DATE - INTERVAL '45 days', CURRENT_DATE + INTERVAL '14 days', '1 day') AS d
WHERE b.active = true;

COMMIT;