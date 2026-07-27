"""
catalog.py — Catalogue vendable dynamique (PostgreSQL).
=======================================================
Le Stratège recommandait jusqu'ici des produits écrits en dur dans son prompt
(iPhone 16 Pro, AirPods Pro 3, Forfait 5G Max…). Aucun n'existe dans
sales.produits : le parc réel de la boutique I63 tourne autour de terminaux
Samsung / Huawei / iPhone plus anciens. L'agent recommandait donc, à chaque
cycle, des produits introuvables en boutique — une hallucination inscrite dans
le prompt lui-même.

Ce module construit le catalogue à partir des seules sources de vérité :

  sales.vw_stock_enriched      → ce qui est réellement en stock ici et maintenant
                                 (prix TTC, marge, gamme, niveau de risque)
  sales.vw_top_products        → ce qui se vend réellement (CA et volume 30 j)
  inventory.vw_active_promotions → les remises réellement actives aujourd'hui

Sortie : un bloc texte injecté dans le system prompt (slot {catalog}) et un dict
exploitable par les fallbacks — plus aucun produit ni prix codé en dur.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from app.core.db import acquire

logger = logging.getLogger(__name__)

# Ordre d'affichage : ce qui porte le CA d'abord, les compléments ensuite.
_GAMME_ORDER = [
    "TERMINAL", "FORFAIT", "FORFAIT_BOX", "ACCESSOIRE_PREMIUM", "ACCESSOIRE",
    "SERVICE", "SERVICE_DIGITAL", "BUSINESS", "SIM_KIT", "SIM_SERVICE",
    "RECHARGE", "TELECOM_DIRECT",
]

# Rôle commercial de chaque gamme — décrit la mécanique de vente, jamais un
# produit : le LLM choisit le produit dans les lignes réelles fournies dessous.
_GAMME_ROLE = {
    "TERMINAL":           "panier élevé — 1 vente peut couvrir un gap important",
    "FORFAIT":            "revenu récurrent — conversion prépayé → forfait",
    "FORFAIT_BOX":        "revenu récurrent foyer — cross-sell sur vente mobile",
    "ACCESSOIRE_PREMIUM": "marge la plus haute — attacher à chaque vente terminal",
    "ACCESSOIRE":         "complément rapide — closing express",
    "SERVICE":            "marge haute, vente en 60 s — attacher systématiquement",
    "SERVICE_DIGITAL":    "abonnement additionnel — cross-sell sans stock",
    "BUSINESS":           "clients pro — ticket élevé, cycle plus long",
    "SIM_KIT":            "acquisition — porte d'entrée vers forfait",
    "SIM_SERVICE":        "acte de service — occasion de rebond commercial",
    "RECHARGE":           "flux quotidien — volume, marge faible",
    "TELECOM_DIRECT":     "prestation directe",
}

_MAX_PER_GAMME = 5
_CACHE_TTL_S   = 600
_cache: Dict[str, tuple] = {}          # store_id → (catalogue, timestamp)


# ── Requêtes PostgreSQL ───────────────────────────────────────────────────────

_SQL_CATALOG = """
    WITH ventes AS (
        SELECT sku, SUM(ca_30j) AS ca_30j, SUM(qty_30j) AS qty_30j
        FROM sales.vw_top_products
        WHERE store_id = $1
        GROUP BY sku
    )
    SELECT s.sku,
           s.product_name,
           s.gamme_libelle,
           s.prix_ttc,
           s.marge_pct,
           s.stock_dispo,
           s.stock_risk,
           COALESCE(v.ca_30j, 0)  AS ca_30j,
           COALESCE(v.qty_30j, 0) AS qty_30j
    FROM sales.vw_stock_enriched s
    LEFT JOIN ventes v ON v.sku = s.sku
    WHERE s.store_id = $1
      AND s.stock_dispo > 0
      AND s.prix_ttc   > 0
      AND COALESCE(s.actif, TRUE)
    ORDER BY s.gamme_libelle,
             COALESCE(v.ca_30j, 0) DESC,
             s.prix_ttc * (1 + s.marge_pct / 100.0) DESC
"""

# Promotions du jour : soit ciblées SKU, soit libellées sur une famille produit.
_SQL_PROMOS = """
    SELECT promo_name, product_name, discount_pct, end_date
    FROM inventory.vw_active_promotions
    ORDER BY discount_pct DESC NULLS LAST
    LIMIT 6
"""

# Ce qui manque : utile au Stratège pour ne PAS pousser un produit indisponible
# et pour justifier une bascule vers une alternative.
_SQL_RUPTURES = """
    SELECT s.product_name, s.gamme_libelle, s.prix_ttc, s.stock_dispo, s.stock_risk
    FROM sales.vw_stock_enriched s
    WHERE s.store_id = $1
      AND s.prix_ttc > 0
      AND s.stock_risk IN ('rupture', 'critical')
    ORDER BY s.prix_ttc DESC
    LIMIT 8
"""

# Références déréférencées (actif = FALSE) encore physiquement en stock.
# Chez I63 ce sont 43 terminaux pour ~655 unités : du capital immobilisé qui ne
# s'est pas vendu depuis 30 jours. C'est un levier commercial réel — écouler —
# mais il doit être présenté comme tel, jamais mélangé au catalogue courant.
_SQL_DORMANT = """
    SELECT s.product_name, s.gamme_libelle, s.prix_ttc, s.stock_dispo,
           s.prix_ttc * s.stock_dispo AS valeur_immobilisee
    FROM sales.vw_stock_enriched s
    WHERE s.store_id = $1
      AND s.stock_dispo > 0
      AND s.prix_ttc   > 0
      AND s.actif IS FALSE
    ORDER BY valeur_immobilisee DESC
    LIMIT 6
"""


async def fetch_catalog(store_id: str, force: bool = False) -> Dict[str, Any]:
    """Catalogue vendable du magasin, mis en cache 10 min."""
    cached = _cache.get(store_id)
    if cached and not force and (time.time() - cached[1]) < _CACHE_TTL_S:
        return cached[0]

    catalog: Dict[str, Any] = {
        "store_id": store_id, "gammes": {}, "promotions": [],
        "ruptures": [], "dormant": [], "nb_skus": 0, "source": "postgresql",
    }
    try:
        async with acquire(connect_timeout=5) as conn:
            rows     = await conn.fetch(_SQL_CATALOG, store_id)
            promos   = await conn.fetch(_SQL_PROMOS)
            ruptures = await conn.fetch(_SQL_RUPTURES, store_id)
            dormant  = await conn.fetch(_SQL_DORMANT, store_id)

        for r in rows:
            # Le référentiel contient des libellés vides ou réduits à « . »
            # (lignes de recharge historiques). Un produit sans nom lisible ne
            # peut ni être proposé au client ni être rapproché d'une sortie LLM.
            nom = str(r["product_name"] or "").strip()
            if len(nom.strip(".- ")) < 3:
                continue
            gamme = r["gamme_libelle"] or "AUTRE"
            bucket = catalog["gammes"].setdefault(gamme, [])
            if len(bucket) >= _MAX_PER_GAMME:
                continue
            bucket.append({
                "sku":        int(r["sku"]),
                "nom":        nom,
                "prix_ttc":   round(float(r["prix_ttc"]), 2),
                "marge_pct":  round(float(r["marge_pct"] or 0), 1),
                "stock":      int(r["stock_dispo"]),
                "stock_risk": str(r["stock_risk"]),
                "ca_30j":     round(float(r["ca_30j"] or 0), 2),
                "qty_30j":    int(r["qty_30j"] or 0),
            })
        catalog["nb_skus"] = sum(len(v) for v in catalog["gammes"].values())

        catalog["promotions"] = [
            {"nom": str(p["promo_name"]), "produit": str(p["product_name"] or ""),
             "remise_pct": round(float(p["discount_pct"] or 0), 1),
             "fin": str(p["end_date"] or "")}
            for p in promos
        ]
        catalog["ruptures"] = [
            {"nom": str(r["product_name"]), "gamme": r["gamme_libelle"] or "AUTRE",
             "prix_ttc": round(float(r["prix_ttc"]), 2),
             "stock": int(r["stock_dispo"]), "risk": str(r["stock_risk"])}
            for r in ruptures
        ]
        catalog["dormant"] = [
            {"nom": str(d["product_name"]), "gamme": d["gamme_libelle"] or "AUTRE",
             "prix_ttc": round(float(d["prix_ttc"]), 2), "stock": int(d["stock_dispo"]),
             "valeur": round(float(d["valeur_immobilisee"]), 2)}
            for d in dormant
        ]

        logger.info("[STRATEGE CATALOG] %s — %d SKU / %d gammes | %d promo(s) | "
                    "%d rupture(s) | %d dormant(s)",
                    store_id, catalog["nb_skus"], len(catalog["gammes"]),
                    len(catalog["promotions"]), len(catalog["ruptures"]),
                    len(catalog["dormant"]))
    except Exception as e:
        logger.warning("[STRATEGE CATALOG] %s indisponible: %s", store_id, str(e)[:120])
        catalog["source"] = "unavailable"

    _cache[store_id] = (catalog, time.time())
    return catalog


# ── Rendu pour le prompt ──────────────────────────────────────────────────────

def format_catalog_block(catalog: Dict[str, Any]) -> str:
    """
    Rend le catalogue en texte compact pour le system prompt.

    Sans catalogue lisible, le LLM invente : le bloc est donc explicite sur le
    fait que cette liste est exhaustive et fermée.
    """
    gammes = catalog.get("gammes") or {}
    if not gammes:
        return (
            "CATALOGUE INDISPONIBLE — aucune donnée stock accessible.\n"
            "Ne cite AUCUN nom de produit ni prix. Formule les actions par "
            "gamme commerciale uniquement (terminal, forfait, service, accessoire)."
        )

    lines = [
        f"CATALOGUE RÉEL EN STOCK — boutique {catalog.get('store_id','?')} "
        f"({catalog.get('nb_skus',0)} références disponibles maintenant)",
        "Liste FERMÉE : tout produit ou prix absent de ces lignes n'existe pas.",
        "",
    ]

    ordered = [g for g in _GAMME_ORDER if g in gammes]
    ordered += [g for g in gammes if g not in _GAMME_ORDER]

    for gamme in ordered:
        produits = gammes[gamme]
        if not produits:
            continue
        role = _GAMME_ROLE.get(gamme, "")
        lines.append(f"{gamme}" + (f"  ({role})" if role else ""))
        for p in produits:
            flags = []
            if p["stock_risk"] in ("critical", "rupture"):
                flags.append(f"⚠ stock {p['stock']}")
            if p["qty_30j"] > 0:
                flags.append(f"{p['qty_30j']} vendus/30j")
            if p["marge_pct"] >= 25:
                flags.append(f"marge {p['marge_pct']:.0f}%")
            suffix = ("  [" + " · ".join(flags) + "]") if flags else ""
            lines.append(f"  • {p['nom']}  {p['prix_ttc']:.0f} TND{suffix}")
        lines.append("")

    promos = catalog.get("promotions") or []
    if promos:
        lines.append("PROMOTIONS ACTIVES AUJOURD'HUI (source inventory.promotions)")
        for p in promos:
            lines.append(f"  • {p['nom']} — {p['produit']} · -{p['remise_pct']:.0f}% "
                         f"jusqu'au {p['fin']}")
        lines.append("")

    dormant = catalog.get("dormant") or []
    if dormant:
        total = sum(d["valeur"] for d in dormant)
        lines.append(
            f"STOCK DORMANT À ÉCOULER — références déréférencées encore en boutique "
            f"({total:,.0f} TND immobilisés)".replace(",", " ")
        )
        lines.append("  Vendables sur place, mais hors catalogue courant : à proposer "
                     "en déstockage ou reprise, jamais comme nouveauté.")
        for d in dormant:
            lines.append(f"  • {d['nom']}  {d['prix_ttc']:.0f} TND × {d['stock']} en stock")
        lines.append("")

    ruptures = catalog.get("ruptures") or []
    if ruptures:
        lines.append("NE PAS POUSSER — stock critique ou rupture (proposer une alternative en stock)")
        for r in ruptures[:6]:
            etat = "rupture" if r["stock"] == 0 else f"{r['stock']} restant(s)"
            lines.append(f"  • {r['nom']} — {etat}")
        lines.append("")

    return "\n".join(lines)


def pick_products(catalog: Dict[str, Any], gammes: List[str], limit: int = 3,
                  exclude_critical: bool = True) -> List[Dict[str, Any]]:
    """
    Sélectionne des produits réels pour les fallbacks déterministes.

    Priorité : rotation prouvée (CA 30 j) puis valeur × marge — donc ce qui se
    vend ici, pas ce qui a l'air attractif sur le papier.
    """
    candidats: List[Dict[str, Any]] = []
    for gamme in gammes:
        for p in (catalog.get("gammes") or {}).get(gamme, []):
            if exclude_critical and p["stock_risk"] in ("rupture", "critical"):
                continue
            candidats.append({**p, "gamme": gamme})
    candidats.sort(key=lambda p: (p["ca_30j"], p["prix_ttc"] * (1 + p["marge_pct"] / 100)),
                   reverse=True)
    return candidats[:limit]
