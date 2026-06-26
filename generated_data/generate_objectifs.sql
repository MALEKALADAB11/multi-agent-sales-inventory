-- Objectifs journaliers pour toutes les boutiques actives
BEGIN;
DELETE FROM sales.objectifs WHERE date_objectif >= CURRENT_DATE - INTERVAL '45 days';

INSERT INTO sales.objectifs (store_id, agent_id, date_objectif, objectif_ca, objectif_transactions, objectif_panier_moyen)
SELECT
    b.store_id,
    NULL,
    d::date,
    CASE
        WHEN b.ville IN ('TUNIS', 'Tunis') THEN 1200 + (random() * 400)::int
        WHEN b.ville IN ('SOUSSE', 'Sousse', 'SFAX', 'Sfax') THEN 1000 + (random() * 300)::int
        ELSE 700 + (random() * 300)::int
    END,
    CASE
        WHEN b.ville IN ('TUNIS', 'Tunis') THEN 12 + (random() * 8)::int
        ELSE 8 + (random() * 6)::int
    END,
    65 + (random() * 35)::int
FROM sales.boutiques b
CROSS JOIN generate_series(CURRENT_DATE - INTERVAL '45 days', CURRENT_DATE + INTERVAL '14 days', '1 day') AS d
WHERE b.active = true;

COMMIT;