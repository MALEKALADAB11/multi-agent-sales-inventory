"""
rag/query.py — Préparation de la requête avant retrieval.

Le conseiller tape « client trouve ça cher » ; le corpus dit « objection prix ».
Le dense rattrape une partie de cet écart, le BM25 non. L'expansion de requête
injecte le vocabulaire du corpus dans la requête : c'est le levier de recall le
moins cher et le plus fiable, bien avant tout HyDE ou multi-query LLM.
"""

import re
import unicodedata
from datetime import datetime

from app.sales.data.rag.settings import (
    ALL_DOMAINS,
    DOMAIN_DECISION,
    DOMAIN_INVENTORY_PLAYBOOK,
    DOMAIN_PRODUCT,
    DOMAIN_SALES_SCRIPT,
)


def normalize(text: str) -> str:
    """Minuscules + suppression des accents (aligné sur l'asciifolding Milvus)."""
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


# Terme déclencheur → vocabulaire du corpus à injecter.
# Gauche : ce que tape un humain. Droite : ce qu'écrit le corpus.
_EXPANSIONS: dict[str, str] = {
    # ── Vente ────────────────────────────────────────────────────────────────
    "cher":          "objection prix budget trop cher argument valeur",
    "hesite":        "hesitation indecision closing choix limite",
    "reflechir":     "objection report closing urgence rarete",
    "concurrent":    "orange tunisie telecom comparaison differenciation",
    "reduction":     "remise promotion discount negociation marge",
    "vendre plus":   "upsell montee en gamme bundle accessoire",
    "objectif":      "gap objectif ca cible performance",
    "gap":           "gap objectif ecart cible retard ca",
    "closing":       "conclusion vente signature engagement",
    "forfait":       "abonnement plan data voix sms",
    "recharge":      "credit prepaye top up",
    "bundle":        "pack offre groupee accessoire attache",
    "accessoire":    "bundle attache coque protection ecouteurs upsell",
    "upsell":        "montee en gamme premium superieur",
    "client":        "prospect visiteur acheteur",

    # ── Stock / approvisionnement ────────────────────────────────────────────
    "rupture":       "stock epuise out of stock indisponible rupture imminente",
    "stock bas":     "sous stock seuil reappro alerte critique",
    "reappro":       "reapprovisionnement commande fournisseur purchase order",
    "commande":      "purchase order bon de commande po fournisseur",
    "po":            "purchase order bon de commande approvisionnement",
    "surstock":      "sur stock overstock dormant rotation lente ecoulement",
    "fournisseur":   "supplier lead time delai livraison moq",
    "moq":           "quantite minimale commande minimum order quantity",
    "lead time":     "delai livraison approvisionnement fournisseur",
    "substitution":  "produit alternatif equivalent remplacement report vente",
    "kanban":        "bon de commande po suggere approuve workflow",
    "alerte":        "alert seuil critique urgence stock",
    "peremption":    "obsolescence fin de vie eol demarque",

    # ── Catalogue (questions factuelles : prix, marge, specs) ────────────────
    "combien":       "prix tarif tnd cout",
    "coute":         "prix tarif tnd",
    "prix":          "tarif tnd prix ttc",
    "marge":         "marge pct rentabilite benefice",
    "stock de":      "quantite disponible stock",

    # ── Cross-domaine (le cœur du coach) ─────────────────────────────────────
    "pousser":       "produit a pousser recommandation marge stock disponible",
    "quoi vendre":   "produit recommande stock disponible marge promotion",
}

# Un terme du corpus, quand il apparaît, indique le domaine à interroger.
_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    DOMAIN_INVENTORY_PLAYBOOK: (
        "stock", "rupture", "reappro", "commande", "fournisseur", "surstock",
        "moq", "lead time", "kanban", "po", "livraison", "inventaire", "alerte",
    ),
    DOMAIN_PRODUCT: (
        "prix", "marge", "sku", "iphone", "samsung", "galaxy", "modele",
        "reference", "catalogue", "promo", "combien coute", "quel produit",
    ),
    DOMAIN_DECISION: (
        "derniere fois", "historique", "deja", "marche", "fonctionne",
        "resultat", "impact", "hier", "semaine derniere",
    ),
}


def expand(query: str) -> str:
    """Requête + vocabulaire du corpus pour les termes reconnus. Idempotent."""
    norm = normalize(query)
    extras: list[str] = []
    for trigger, vocab in _EXPANSIONS.items():
        if trigger in norm:
            extras.append(vocab)
    if not extras:
        return query.strip()
    # Dédupe en conservant l'ordre : la requête d'origine reste en tête, donc
    # dominante pour BM25.
    seen: set[str] = set(norm.split())
    tail: list[str] = []
    for word in " ".join(extras).split():
        if word not in seen:
            seen.add(word)
            tail.append(word)
    return f"{query.strip()} {' '.join(tail)}"


def domains_for(query: str) -> list[str]:
    """
    Domaines à interroger : tous, toujours.

    Restreindre les domaines sur des mots-clés paraissait économe, mais coupait
    le coach de ses faits. Mesuré sur « le Galaxy S25 est en rupture, je propose
    quoi ? » : l'indice « rupture » sélectionnait les playbooks stock et excluait
    les fiches produit, donc aucun substitut réel n'était fourni — le LLM en a
    inventé un, avec un SKU et un prix faux.

    La recherche par domaine (retriever._fan_out) empêche la famine, le plancher
    de pertinence écarte le hors-sujet, et le fan-out complet coûte quelques
    dizaines de millisecondes. Il n'y a rien à gagner à deviner les domaines.
    """
    return list(ALL_DOMAINS)


def preferred_domains(query: str) -> tuple[str, ...]:
    """
    Domaines que la requête *suggère*. Sert de bonus au rerank, pas de filtre :
    un signal faible ne doit jamais faire disparaître un document.
    """
    norm = normalize(query)
    hinted = {DOMAIN_SALES_SCRIPT}   # le coach reste un coach de vente
    for domain, hints in _DOMAIN_HINTS.items():
        if any(h in norm for h in hints):
            hinted.add(domain)
    return tuple(sorted(hinted))


def build_context_query(
    *,
    gap_pct: float = 0.0,
    hour: int | None = None,
    urgency: str = "MEDIUM",
    weather_effect: float = 0.0,
    advisor_name: str = "",
    critical_stock: int = 0,
) -> str:
    """
    Requête synthétique quand personne n'a rien tapé : le cycle autonome doit
    quand même récupérer des scripts pertinents pour la situation du moment.
    """
    if hour is None:
        hour = datetime.now().hour

    parts: list[str] = []

    if gap_pct > 60:
        parts.append("gap critique objectif tres eloigne urgence")
    elif gap_pct > 30:
        parts.append("gap modere performance insuffisante")
    else:
        parts.append("gap faible objectif proche consolidation")

    if 14 <= hour <= 17:
        parts.append("heure de pointe pic ventes affluence")
    elif 19 <= hour <= 20:
        parts.append("soiree dernieres heures fermeture")
    elif hour <= 10:
        parts.append("matin ouverture boutique faible trafic")

    if weather_effect <= -0.15:
        parts.append("meteo defavorable pluie trafic reduit")

    if urgency == "HIGH":
        parts.append("action immediate priorite")

    if critical_stock > 0:
        parts.append("rupture stock imminente substitution produit alternatif")

    if advisor_name:
        parts.append(f"coaching conseiller {advisor_name}")

    return " ".join(parts)
