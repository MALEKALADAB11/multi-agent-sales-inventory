# Implémentation: Chart d'Évolution du % Critique

## Contexte et Objectif

### Objectif
Ajouter un mini chart (line/area chart) dans la page inventory qui montre l'évolution du pourcentage de produits critiques sur les dernières 24h ou 48h.

**Question posée par le chart:** "Est-ce que la situation s'améliore ou s'aggrave?"

### Définition de "Produit Critique"

Le % critique = (nombre de produits critiques / nombre total de produits) × 100

### Système Actuel
- Le calcul du `riskLevel` (critical/high/medium/low) existe déjà dans `app/inventory/api/routes.py`
- Le frontend reçoit déjà la liste des produits avec leur `riskLevel`
- Le stock est mis à jour en temps réel via:
  - **Ventes**: Trigger `sync_stock_on_sale` (migration 0012) → décrémente `inventory.stock_levels`
  - **Réceptions**: `StockSimulator.record_reception()` → incrémente le stock
  - **Tous les mouvements** sont tracés dans `supply.stock_movements` avec `stock_avant` et `stock_apres`

---

## Migrations Existantes Pertinentes

### Migration 0012: Vente Stock Movements
**Fichier:** `db/migrations/versions/0012_vente_stock_movements.py`

Cette migration ajoute le trigger `sync_stock_on_sale` qui:
- Décrémente automatiquement `inventory.stock_levels.quantity` lors d'une vente
- Écrit un mouvement dans `supply.stock_movements` avec:
  - `type_mouvement = 'VENTE'`
  - `stock_avant` et `stock_apres`
  - `reference_id = sale_id`
  - `reference_type = 'VENTE'`

**Impact:** Chaque vente est tracée avec l'état du stock avant/après, ce qui permet de reconstruire l'historique.

### Migration 0010: PO Delivery Tracking
**Fichier:** `db/migrations/versions/0010_po_delivery_tracking.py`

Cette migration ajoute des colonnes à `supply.purchase_orders`:
- `date_soumission` - Timestamp de passage en SOUMIS
- `date_confirmation` - Timestamp de passage en CONFIRME
- `confirmed_auto` - Booléen pour distinguer confirmation auto/humaine
- `ecart_livraison_jours` - Écart entre livraison réelle et prévue

**Impact:** Permet de suivre le cycle de vie des commandes et les réceptions.

### Table supply.stock_movements (Baseline 0001)
**Fichier:** `db/migrations/versions/sql/0001_baseline.sql` (lignes 2365-2386)

Structure de la table:
```sql
CREATE TABLE supply.stock_movements (
    mouvement_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    sku integer NOT NULL,
    store_id character varying(50) NOT NULL,
    type_mouvement character varying(30) NOT NULL,  -- VENTE, RECEPTION_BC, etc.
    quantite integer NOT NULL,
    stock_avant integer,
    stock_apres integer,
    reference_id character varying(100),
    reference_type character varying(30),
    agent_id integer,
    date_mouvement timestamp without time zone DEFAULT now(),
    notes text,
    created_at timestamp without time zone DEFAULT now()
);
```

**Index:**
- `idx_mvt_sku_store` sur (sku, store_id, date_mouvement DESC)
- `idx_mvt_store_date` sur (store_id, date_mouvement DESC)

**Impact:** C'est la source principale de données pour Option 1 (reconstruction on-demand).

### Problème
Actuellement, le % critique est calculé **à la demande** quand le frontend appelle l'endpoint d'analyse. Il n'y a **pas d'historique** stocké, donc impossible de voir l'évolution temporelle.

---

## Deux Options d'Implémentation

### Option 1: Reconstruction On-Demand depuis `stock_movements`

#### Principe
À chaque appel du nouvel endpoint, on reconstruit l'historique du % critique en parcourant `supply.stock_movements` et en regroupant par heure.

#### Avantages
- ✅ **Pas de nouvelle table** - utilise l'existant
- ✅ **Pas de job cron** - pas de maintenance supplémentaire
- ✅ **Flexibilité** - le frontend peut demander n'importe quelle période (24h, 48h, 7 jours)
- ✅ **Données réelles** - basé sur les mouvements réels stockés

#### Inconvénients
- ❌ **Performance** - recalcul à chaque appel (peut être lent si beaucoup de données)
- ❌ **Complexité SQL** - requête avec window functions pour reconstruire l'état

#### Implémentation SQL (exemple)
```sql
-- Reconstruire l'historique du % critique par heure
SELECT 
    DATE_TRUNC('hour', date_mouvement) AS hour,
    COUNT(DISTINCT sku) AS total_skus,
    COUNT(DISTINCT CASE WHEN stock_apres <= 3 THEN sku END) AS critical_skus,
    ROUND(
        CASE WHEN COUNT(DISTINCT sku) > 0
             THEN (COUNT(DISTINCT CASE WHEN stock_apres <= 3 THEN sku END)::float / 
                   COUNT(DISTINCT sku)) * 100
             ELSE 0 END,
        2
    ) AS critical_pct
FROM supply.stock_movements
WHERE store_id = $1
  AND date_mouvement >= NOW() - ($2 || ' hours')::interval
GROUP BY DATE_TRUNC('hour', date_mouvement)
ORDER BY hour DESC
```

#### Fichiers à modifier
1. `app/inventory/repositories/inventory_repo.py`
   - Ajouter méthode `get_critical_percentage_history(store_id, hours_back)`

2. `app/inventory/api/routes.py`
   - Ajouter endpoint `GET /store/{store_id}/critical-trend`

---

### Option 2: Snapshots Réguliers (Nouvelle Table)

#### Principe
Créer une table dédiée qui stocke un snapshot du % critique à intervalles réguliers (ex: toutes les heures). Un job cron calcule et insère ces snapshots.

#### Avantages
- ✅ **Performance** - lecture très rapide (simple SELECT)
- ✅ **Simplicité** - pas de reconstruction complexe
- ✅ **Historique persistant** - données toujours disponibles

#### Inconvénients
- ❌ **Nouvelle table** - migration DB requise
- ❌ **Job cron** - maintenance supplémentaire
- ❌ **Résolution limitée** - 1 snapshot/heure (ou autre intervalle fixe)
- ❌ **Données en retard** - le dernier snapshot peut avoir X heures de retard

#### Implémentation SQL (nouvelle table)
```sql
-- Migration 0017_critical_trend_history
CREATE TABLE inventory.critical_trend_history (
    id SERIAL,
    store_id TEXT NOT NULL,
    snapshot_time TIMESTAMP NOT NULL,
    total_products INT,
    critical_count INT,
    critical_pct NUMERIC(5,2),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_cth_store_time ON inventory.critical_trend_history(store_id, snapshot_time DESC);

-- Job cron (ex: toutes les heures)
INSERT INTO inventory.critical_trend_history (store_id, snapshot_time, total_products, critical_count, critical_pct)
SELECT 
    store_id,
    NOW() AS snapshot_time,
    COUNT(*) AS total_products,
    COUNT(CASE WHEN quantity <= 3 THEN 1 END) AS critical_count,
    ROUND(
        CASE WHEN COUNT(*) > 0
             THEN (COUNT(CASE WHEN quantity <= 3 THEN 1 END)::float / COUNT(*)) * 100
             ELSE 0 END,
        2
    ) AS critical_pct
FROM inventory.stock_levels
GROUP BY store_id;
```

#### Fichiers à modifier/créer
1. `db/migrations/versions/0017_critical_trend_history.py` - Nouvelle migration
2. `scripts/critical_trend_snapshot.py` - Script pour le job cron
3. `app/inventory/repositories/inventory_repo.py`
   - Ajouter méthode `get_critical_trend_history(store_id, hours_back)`
4. `app/inventory/api/routes.py`
   - Ajouter endpoint `GET /store/{store_id}/critical-trend`

---

## Fichiers Impliqués (Communs aux deux options)

### Fichiers existants à utiliser
- `supply.stock_movements` - Journal des mouvements de stock (Option 1)
- `inventory.stock_levels` - État actuel du stock (Option 2)
- `inventory.products` - Jointure pour filtrer les produits valides

### Fichiers à modifier
1. **`app/inventory/repositories/inventory_repo.py`**
   - Ajouter la méthode de récupération de l'historique
   - Utiliser `asyncpg` pour les requêtes asynchrones

2. **`app/inventory/api/routes.py`**
   - Ajouter le nouvel endpoint
   - Intégrer avec le système de cache existant (optionnel)

### Fichiers optionnels (Option 2 uniquement)
3. **`db/migrations/versions/0017_critical_trend_history.py`** - Nouvelle migration
4. **`scripts/critical_trend_snapshot.py`** - Job cron pour les snapshots

---
## Format de Réponse attendu

Le nouvel endpoint doit retourner:

```json
{
  "store_id": "S01",
  "hours_back": 48,
  "data": [
    {
      "hour": "2026-08-02T10:00:00",
      "total_skus": 150,
      "critical_skus": 15,
      "critical_pct": 10.0
    },
    {
      "hour": "2026-08-02T09:00:00",
      "total_skus": 150,
      "critical_skus": 18,
      "critical_pct": 12.0
    }
    // ... plus de points
  ]
}
```

Le frontend peut ensuite utiliser ces données pour dessiner un line/area chart montrant l'évolution du `critical_pct` dans le temps.
