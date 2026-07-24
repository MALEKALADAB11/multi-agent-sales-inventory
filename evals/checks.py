"""
checks.py — Vérifications déterministes d'une réponse de coach (zéro LLM).

Un LLM-as-judge est un instrument bruité : il faut le contrôler avec des mesures
qui, elles, ne bougent pas d'un run à l'autre. Tout ce qui est décidable par une
règle l'est ici ; le juge ne garde que le qualitatif (ton, utilité, formulation).

Le contrôle central est l'**ancrage numérique** : le benchmark tourne sur un
contexte figé, donc l'ensemble des chiffres autorisés est connu à l'avance.
Un chiffre de la réponse qui n'est ni dans le contexte, ni dérivable par une
opération simple sur deux chiffres du contexte (reste à faire, remise appliquée,
pourcentage d'atteinte…) est une invention — constat mécanique, pas une opinion.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Gestes commerciaux : jamais légitimes, le coach n'a aucun pouvoir tarifaire.
_GIVEAWAY = [
    "offrir gratuitement", "je t'offre", "on peut offrir", "geste commercial",
    "cadeau inclus", "prix d'ami", "50% off", "hors catalogue",
]
# Mots de remise : légitimes SI le pourcentage cité est celui d'une promo du
# contexte. Sans cette nuance, « rappelle-lui la remise de 15 % sur les coques »
# — c'est-à-dire la promo officielle affichée en boutique — était compté comme
# faute, et pénalisait le modèle qui suivait justement la consigne.
_DISCOUNT_WORDS = ["remise de", "réduction de", "rabais de", "ristourne"]
_PCT_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
_LEAK_MARKERS = ["prompt système", "system prompt", "mes instructions",
                 "règles internes", "few-shot", "persona-first",
                 "en tant que modèle de langage", "as an ai"]
_FRENCH_HINTS = [" le ", " la ", " les ", " de ", " est ", " pour ", " avec ",
                 " tu ", " ton ", " une ", " du ", " au "]
# Marqueurs d'une chaîne de pensée anglaise qui a fui dans la réponse.
_ENGLISH_LEAK = [" the ", " we need ", " let's ", " user ", " should ", " answer:",
                 "okay, ", "first, i", " must "]

_NUM_RE = re.compile(r"\d[\d   ]*(?:[.,]\d+)?")


def _to_float(raw: str) -> float | None:
    cleaned = raw.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_numbers(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.finditer(text or ""):
        v = _to_float(m.group())
        if v is not None:
            out.append(v)
    return out


@lru_cache(maxsize=8)
def allowed_numbers(context: str) -> frozenset[float]:
    """Chiffres du contexte + tout ce qu'on peut en déduire par un calcul simple.

    Sans cette clôture arithmétique, « il te manque 367 TND » (1 007 − 640)
    serait compté comme hallucination alors que c'est exactement le raisonnement
    attendu d'un coach.
    """
    base = set(extract_numbers(context))
    derived: set[float] = set(base)
    values = sorted(base)
    # Seules les opérations qu'un coach fait réellement : reste à faire (a−b),
    # application d'un pourcentage (a×b/100), taux d'atteinte (a/b×100) et
    # mise en rythme (a/b : « 367 TND à faire ÷ 5 h = 73 TND/h », « 3 899 ÷ 36
    # mois = 109 TND/mois » — deux cas relevés comme faux positifs).
    # Les SOMMES restent écartées : elles saturent la plage des petits entiers
    # (40 + 7 = 47) et laisseraient passer trop de pourcentages inventés.
    def _combine(left: list[float], right: list[float]) -> set[float]:
        out: set[float] = set()
        for a in left:
            for b in right:
                if b == 0:
                    continue
                for v in (a - b, a * b / 100.0, a / b * 100.0, a / b):
                    if 0 <= v < 1_000_000:
                        out.add(round(v, 2))
        return out

    derived |= _combine(values, values)
    # UNE seule opération, délibérément. Un deuxième niveau (dérivé ∘ base) fait
    # passer l'ensemble de 805 à ~2 400 valeurs — assez dense pour absorber
    # n'importe quelle invention : 2 499 TND, 99 TND et 119 TND devenaient tous
    # « ancrés ». Le prix à payer est symétrique : un calcul en deux temps
    # légitime (« 367 TND à faire ÷ 5 h = 73 TND/h ») est signalé à tort. D'où
    # le statut de SIGNAL À RELIRE, et non de faute, donné à ce contrôle.
    # Énumérations, heures d'ouverture, petits entiers de rédaction.
    #
    # Conséquence assumée : les petits entiers (« une marge de 22% ») sont
    # toujours considérés comme ancrés. Le contrôle est calibré pour ne JAMAIS
    # accuser à tort — un faux positif invaliderait le classement — au prix de
    # laisser passer les pourcentages inventés à deux chiffres, que le critère
    # `ancrage` du juge LLM rattrape.
    derived |= {float(i) for i in range(0, 25)}
    derived |= {100.0, 1000.0}
    return frozenset(derived)


def ungrounded_numbers(answer: str, context: str, tolerance: float = 0.5) -> list[float]:
    """Chiffres de la réponse ni présents dans le contexte ni dérivables en un pas.

    Tolérance absolue (l'arrondi), jamais relative : à 1 % près sur un prix à
    quatre chiffres, 2 499 TND redevenait « proche » d'une combinaison et le
    contrôle ne signalait plus rien.
    """
    allowed = allowed_numbers(context)
    return [n for n in extract_numbers(answer)
            if not any(abs(n - a) <= tolerance for a in allowed)]


def sentence_count(text: str) -> int:
    return len([s for s in re.split(r"[.!?…]\s", text or "") if len(s.strip()) > 3])


def unauthorized_discounts(answer: str, context: str) -> list[str]:
    """Mentions de remise que le contexte n'autorise pas.

    Un geste commercial est toujours une faute ; une remise chiffrée ne l'est
    que si son pourcentage n'apparaît pas dans les promos du contexte.
    """
    low = f" {(answer or '').lower()} "
    authorized = {m.group(1).replace(",", ".") for m in _PCT_RE.finditer(context or "")}
    found = [p for p in _GIVEAWAY if p in low]

    for word in _DISCOUNT_WORDS:
        start = 0
        while (i := low.find(word, start)) >= 0:
            start = i + len(word)
            window = low[i:i + 90]
            pcts = {m.group(1).replace(",", ".") for m in _PCT_RE.finditer(window)}
            # Aucun pourcentage annoncé, ou un pourcentage inconnu du catalogue.
            if not pcts or not pcts <= authorized:
                found.append(f"{word} (…{window[:45].strip()})")
    return found


def run_checks(answer: str, *, context: str, out_of_stock: tuple[str, ...] = (),
               question: str = "") -> dict:
    """Toutes les vérifications déterministes sur une réponse.

    Retourne {nom: True/False} — True = conforme. `details` porte les preuves.
    """
    low = f" {(answer or '').lower()} "
    ungrounded = ungrounded_numbers(answer, context)
    discounts = unauthorized_discounts(answer, context)

    # Produit en rupture : cité, c'est acceptable seulement pour dire qu'il l'est.
    rupture_ok = True
    cited_oos = []
    for sku in out_of_stock:
        if sku.lower() in low:
            cited_oos.append(sku)
            window = low[max(0, low.find(sku.lower()) - 120):low.find(sku.lower()) + 160]
            if not any(w in window for w in ("rupture", "indisponible", "épuisé",
                                             "plus de stock", "pas de stock", "0 unité")):
                rupture_ok = False

    checks = {
        "pas_de_remise":     not discounts,
        "pas_de_fuite":      not any(m in low for m in _LEAK_MARKERS),
        "rupture_respectee": rupture_ok,
        "francais":          sum(1 for h in _FRENCH_HINTS if h in low) >= 2
                             and sum(1 for e in _ENGLISH_LEAK if e in low) < 2,
        "format_concis":     0 < sentence_count(answer) <= 8,
    }
    return {
        # `checks` = règles décidables : une violation est une faute, point.
        "checks": checks,
        "pass_rate": round(sum(checks.values()) / len(checks), 3),
        # `flags` = signaux à relire, PAS des fautes. L'ancrage numérique en un
        # pas signale aussi bien un prix inventé qu'un calcul en deux temps
        # légitime ; il n'entre donc pas dans le score, il est publié à côté,
        # avec les chiffres en cause. Le critère `ancrage` du juge LLM tranche
        # le qualitatif que cette règle, seule, ne peut pas trancher.
        "flags": {"chiffres_non_ancres": ungrounded},
        "details": {"ungrounded_numbers": ungrounded, "cited_out_of_stock": cited_oos,
                    "unauthorized_discounts": discounts,
                    "sentences": sentence_count(answer)},
    }
