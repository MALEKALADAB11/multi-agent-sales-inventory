"""
run_models.py — Benchmark comparatif des LLM candidats au rôle de coach.

Protocole à variables contrôlées : même prompt système, même contexte synthétique
figé (CA, objectif, stocks, scripts RAG), mêmes questions, même panel de juges.
Seul le modèle change — le classement mesure donc le modèle, pas la chance du
contexte.

Ce que le banc corrige par rapport à une comparaison naïve « une question, une
réponse, une note » :

1. **Résilience** — retries avec backoff sur 429/5xx. Sans ça, un modèle
   rate-limité rend 3 réponses sur 10 et se retrouve premier avec une moyenne
   calculée sur ses 3 réponses les plus faciles (biais de survie).
2. **Comparaison appariée** — le classement principal ne se calcule que sur les
   questions auxquelles TOUS les modèles ont répondu. Comparer des moyennes sur
   des sous-ensembles différents n'a aucun sens.
3. **Répétitions** — `--repeat` passages par question : on mesure aussi la
   *stabilité* (écart-type intra-modèle), pas seulement la performance moyenne.
4. **Panel de juges** — `--judges` juges distincts (providers différents) par
   réponse, jamais le modèle évalué. Leur désaccord moyen est reporté : un
   classement dont l'écart est inférieur au désaccord des juges ne conclut rien.
5. **Intervalles de confiance** — bootstrap sur la moyenne. Deux modèles dont les
   IC se chevauchent sont déclarés indiscernables plutôt que 1er et 2e.
6. **Contrôles déterministes** — ancrage numérique, remise interdite, produit en
   rupture, fuite de prompt, langue : mesurés par règle (cf. `evals/checks.py`),
   sans LLM, donc reproductibles au caractère près.
7. **Coût et disponibilité** — un modèle se choisit sur qualité × fiabilité ×
   latence × coût. Le score composite agrège les quatre avec des poids explicites.

Modèles couverts (si clés .env présentes) : Mistral (large/small),
Groq (gpt-oss-120b, llama-3.3-70b), OpenRouter (nemotron nano/super).

    python -m evals.run_models [--repeat 2] [--judges 2] [--retries 3] [--pace 0.4]
"""

from __future__ import annotations

import argparse
import sys
import time

from evals.checks import run_checks
from evals.common import (RESULTS_DIR, chat, load_dataset, load_providers,
                          save_results)
from evals.judge import CRITERIA, judge_panel, panel_disagreement
from evals.metrics import bootstrap_ci, latency_summary, mean_std, overlaps

# Contexte synthétique FIGÉ — chaque chiffre cité par un modèle doit venir d'ici.
# C'est ce qui rend le critère `ancrage` mesurable : tout autre chiffre est inventé.
_CONTEXT = """DONNÉES BOUTIQUE (Ooredoo Lac 2, aujourd'hui 15h) :
- CA du jour : 640 TND / objectif 1 007 TND (63%). Il reste 5 heures de vente.
- Stocks : iPhone 16 128Go = 7 unités (prix 3 899 TND TTC) ; Samsung A25 = 0 unité (RUPTURE) ;
  Recharge 10DT = 210 unités ; Coque universelle = 34 unités (prix 39 TND).
- Alerte : Samsung A25 en rupture — ne pas proposer.
- Promo officielle catalogue : -15% sur les coques, affichée en boutique.

SCRIPTS TERRAIN ÉPROUVÉS :
[S1] Objection prix — Reformuler en coût par jour ("moins de 4 TND/jour sur 36 mois"),
     valoriser la reprise de l'ancien téléphone. Impact : +18% de closing.
[S2] Bundle accessoire — Proposer la coque au moment du passage en caisse, pas avant.
     Impact : +9% de panier moyen.
[S3] Client "je vais réfléchir" — Proposer de mettre l'article de côté 24h et prendre
     le numéro. Impact : 40% de retours effectifs."""

_SYSTEM = f"""Tu es le coach commercial des conseillers en boutique Ooredoo Tunisie.
Tu réponds en français, en 3 à 6 phrases, de façon concrète et actionnable.

RÈGLES STRICTES :
- Chaque chiffre (prix, stock, CA) doit provenir des données ci-dessous. Si une
  information manque, dis-le — n'invente jamais.
- Jamais de remise ni d'offre hors promo officielle du catalogue.
- Ne propose jamais un produit en rupture.
- Hors vente/stock/télécom : refuse poliment et recentre.

{_CONTEXT}"""

# Produits que le contexte déclare en rupture — cités hors mention de rupture = faute.
_OUT_OF_STOCK = ("Samsung A25", "A25")

# Tarifs indicatifs USD / 1M tokens (entrée, sortie) au 07/2026. Sert au coût
# comparé, pas à la facturation : à réviser si les grilles bougent.
_PRICES = {
    "mistral-large-latest":                        (2.00, 6.00),
    "mistral-small-latest":                        (0.10, 0.30),
    "open-mistral-nemo":                           (0.15, 0.15),
    "openai/gpt-oss-120b":                         (0.15, 0.75),
    "llama-3.3-70b-versatile":                     (0.59, 0.79),
    "nvidia/nemotron-3-nano-30b-a3b:free":         (0.0, 0.0),
    "nvidia/nemotron-3-super-120b-a12b:free":      (0.0, 0.0),
}

# Poids du score composite — explicites et discutables, c'est le but.
_WEIGHTS = {"qualite": 0.50, "fiabilite": 0.20, "latence": 0.15, "cout": 0.15}


def _price(model: str) -> tuple[float, float]:
    return _PRICES.get(model, (0.0, 0.0))


def _cost_usd(model: str, prompt_tokens: int, output_tokens: int) -> float:
    pin, pout = _price(model)
    return (prompt_tokens * pin + output_tokens * pout) / 1_000_000


def _collect(provider, model, cases, repeat, n_judges, retries, pace) -> dict:
    """Interroge un modèle sur toutes les questions et fait noter chaque réponse."""
    tag = f"{provider.name}/{model}"
    answers: list[dict] = []
    latencies: list[float] = []
    errors = retried = 0
    prompt_tokens = output_tokens = 0

    for case in cases:
        for pass_i in range(repeat):
            res = chat(provider, model,
                       [{"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": case["question"]}],
                       temperature=0.3, max_tokens=500, max_retries=retries)
            retried += int(res.retried)
            if not res.ok:
                errors += 1
                answers.append({"id": case["id"], "pass": pass_i, "error": res.error,
                                "attempts": res.attempts})
                continue
            latencies.append(res.latency_ms)
            prompt_tokens += res.prompt_tokens
            output_tokens += res.output_tokens

            det = run_checks(res.text, context=_CONTEXT, out_of_stock=_OUT_OF_STOCK,
                             question=case["question"])
            panel = judge_panel(case["question"], res.text, context=_CONTEXT,
                                expected_behaviors=case.get("expected_behaviors"),
                                avoid_model=model, n_judges=n_judges,
                                max_retries=retries)

            entry = {"id": case["id"], "pass": pass_i,
                     "latency_ms": round(res.latency_ms), "answer": res.text,
                     "attempts": res.attempts,
                     "checks": det["checks"], "flags": det["flags"],
                     "checks_details": det["details"]}
            if panel:
                scores = {c: round(sum(j.scores[c] for j in panel) / len(panel), 2)
                          for c in CRITERIA if all(c in j.scores for j in panel)}
                mean = round(sum(j.mean for j in panel) / len(panel), 2)
                entry["judge"] = {
                    **scores, "mean": mean,
                    "hallucination": any(j.hallucination for j in panel),
                    "verdict": panel[0].verdict,
                    "judge_model": " + ".join(j.judge_model for j in panel),
                    "n_judges": len(panel),
                    "disagreement": panel_disagreement(panel),
                }
            else:
                entry["judge"] = {"error": "aucun juge disponible"}
            answers.append(entry)
            if pace:
                time.sleep(pace)

    ok = [a for a in answers if "error" not in a]
    judged = [a for a in ok if "mean" in a.get("judge", {})]
    expected = len(cases) * repeat
    cost = _cost_usd(model, prompt_tokens, output_tokens)

    per_check = {}
    if ok:
        for name in ok[0]["checks"]:
            per_check[name] = round(sum(a["checks"][name] for a in ok) / len(ok), 3)

    means = {c: round(sum(a["judge"][c] for a in judged if c in a["judge"])
                      / max(1, sum(1 for a in judged if c in a["judge"])), 2)
             if any(c in a["judge"] for a in judged) else None
             for c in CRITERIA}
    per_answer = [a["judge"]["mean"] for a in judged]
    disagreements = [a["judge"]["disagreement"] for a in judged
                     if a["judge"].get("disagreement") is not None]

    return {
        "model": tag,
        "n_expected": expected,
        "n_answers": len(ok),
        "errors": errors,
        "availability": round(len(ok) / expected, 3) if expected else 0.0,
        "retry_rate": round(retried / expected, 3) if expected else 0.0,
        "latency": latency_summary(latencies),
        "prompt_tokens_total": prompt_tokens,
        "output_tokens_total": output_tokens,
        "cost_usd_total": round(cost, 5),
        "cost_usd_per_1k_answers": round(cost / len(ok) * 1000, 3) if ok else None,
        "scores": means,
        "global_mean": round(sum(per_answer) / len(per_answer), 2) if per_answer else None,
        "stability": mean_std(per_answer),
        "ci95": bootstrap_ci(per_answer),
        "hallucination_rate": round(sum(a["judge"]["hallucination"] for a in judged)
                                    / len(judged), 3) if judged else None,
        "judge_disagreement": round(sum(disagreements) / len(disagreements), 3)
                              if disagreements else None,
        "checks_pass_rate": round(sum(per_check.values()) / len(per_check), 3)
                            if per_check else None,
        "checks": per_check,
        # Hors score : proportion de réponses citant au moins un chiffre non
        # ancré en un pas — à relire, pas à sanctionner (cf. evals/checks.py).
        "ungrounded_flag_rate": (round(sum(bool(a.get("flags", {}).get("chiffres_non_ancres"))
                                           for a in ok) / len(ok), 3) if ok else None),
        "answers": answers,
    }


def _paired(board: list[dict], cases: list[dict]) -> tuple[list[str], dict, list[str]]:
    """Sous-ensemble de questions résolues par TOUS les modèles comparables.

    Un modèle sans aucune réponse (quota épuisé, clé morte) est *exclu* de
    l'intersection au lieu de la vider : sinon un seul modèle indisponible
    supprime la comparaison de qualité pour tous les autres. Il reste dans le
    classement, où sa disponibilité nulle parle d'elle-même.
    """
    eligible = [r for r in board
                if any("mean" in a.get("judge", {}) for a in r["answers"])]
    excluded = [r["model"] for r in board if r not in eligible]

    common = []
    for case in cases:
        if eligible and all(any(a["id"] == case["id"] and "mean" in a.get("judge", {})
                                for a in row["answers"]) for row in eligible):
            common.append(case["id"])
    paired: dict[str, dict] = {}
    for row in eligible:
        vals = [a["judge"]["mean"] for a in row["answers"]
                if a["id"] in common and "mean" in a.get("judge", {})]
        paired[row["model"]] = {
            "global_mean": round(sum(vals) / len(vals), 2) if vals else None,
            "ci95": bootstrap_ci(vals),
            "n": len(vals),
        }
    return common, paired, excluded


def _composite(board: list[dict], paired: dict) -> None:
    """Score de décision 0–1 : qualité, fiabilité, latence, coût, poids explicites."""
    lats = [r["latency"].get("p50_ms") for r in board if r["latency"].get("p50_ms")]
    best_lat = min(lats) if lats else None
    costs = [r["cost_usd_per_1k_answers"] for r in board
             if r["cost_usd_per_1k_answers"]]
    best_cost = min(costs) if costs else None

    for r in board:
        q = paired.get(r["model"], {}).get("global_mean") or r["global_mean"]
        quality = (q / 5.0) if q is not None else 0.0
        # Un modèle qui hallucine ou casse une règle métier ne « compense » pas
        # par la latence : la qualité est pondérée par les contrôles déterministes.
        quality *= (r.get("checks_pass_rate") or 0.0)
        p50 = r["latency"].get("p50_ms")
        latence = (best_lat / p50) if (p50 and best_lat) else 0.0
        c = r["cost_usd_per_1k_answers"]
        cout = 1.0 if not c else (best_cost / c if best_cost else 1.0)
        r["composite"] = round(
            _WEIGHTS["qualite"] * quality
            + _WEIGHTS["fiabilite"] * r["availability"]
            + _WEIGHTS["latence"] * min(latence, 1.0)
            + _WEIGHTS["cout"] * min(cout, 1.0), 3)
        r["quality_norm"] = round(quality, 3)


def _tie_groups(ranking: list[dict], paired: dict) -> list[list[str]]:
    """Regroupe les modèles statistiquement indiscernables (IC qui se chevauchent)."""
    cis = {r["model"]: (paired.get(r["model"], {}).get("ci95") or r["ci95"])
           for r in ranking}
    groups: list[list[str]] = []
    for r in ranking:
        for g in groups:
            if overlaps(cis[r["model"]], cis[g[0]]):
                g.append(r["model"])
                break
        else:
            groups.append([r["model"]])
    return groups


def run(repeat: int = 2, n_judges: int = 2, retries: int = 3,
        pace: float = 0.4, only: str = "", limit: int = 0) -> dict:
    cases = [c for c in load_dataset("coach_qa.json") if c.get("benchmark")]
    if limit:
        cases = cases[:limit]
    providers = load_providers()

    candidates = [(p, m) for p in providers.values() if p.available for m in p.models]
    if only:
        candidates = [(p, m) for p, m in candidates if only in f"{p.name}/{m}"]
    if not candidates:
        print("Aucun provider configuré (clés API absentes du .env).")
        sys.exit(2)

    total_calls = len(cases) * repeat * len(candidates) * (1 + n_judges)
    print("=" * 74)
    print(f"  BENCHMARK MODÈLES — {len(cases)} questions × {len(candidates)} modèles "
          f"× {repeat} passage(s)")
    print(f"  panel de {n_judges} juge(s) distincts · retries={retries} · "
          f"pace={pace}s · ~{total_calls} appels LLM")
    print("=" * 74)

    board: list[dict] = []
    for provider, model in candidates:
        row = _collect(provider, model, cases, repeat, n_judges, retries, pace)
        board.append(row)
        print(f"  {row['model']:<50} score={row['global_mean']}  "
              f"dispo={row['availability']:.0%}  p50={row['latency'].get('p50_ms')}ms  "
              f"checks={row['checks_pass_rate']}  halluc={row['hallucination_rate']}")

    return _finalize(board, cases, repeat=repeat, n_judges=n_judges, retries=retries)


def _finalize(board: list[dict], cases: list[dict], *, repeat: int, n_judges: int,
              retries: int, push: bool = True) -> dict:
    """Agrégation, classement et sauvegarde à partir des réponses déjà collectées.

    Séparé de la collecte pour être rejouable : `--reaggregate` recalcule tout
    (appariement, IC, poids du composite) sur `details` sans redépenser un seul
    appel LLM.
    """
    common, paired, excluded = _paired(board, cases)
    _composite(board, paired)

    def _q(r: dict):
        return paired.get(r["model"], {}).get("global_mean")

    ranking = sorted(board, key=lambda r: (-(r.get("composite") or 0), -(_q(r) or 0)))
    groups = _tie_groups(sorted((r for r in board if _q(r) is not None),
                                key=lambda r: -(_q(r) or 0)), paired)

    print("\n" + "=" * 74)
    print(f"  CLASSEMENT — score composite ({', '.join(f'{k} {v:.0%}' for k, v in _WEIGHTS.items())})")
    print(f"  Qualité mesurée sur les {len(common)}/{len(cases)} questions communes "
          f"aux {len(paired)} modèles comparables")
    if excluded:
        print(f"  Hors comparaison qualité (aucune réponse obtenue) : {', '.join(excluded)}")
    print("=" * 74)
    for i, r in enumerate(ranking, 1):
        ci = paired.get(r["model"], {}).get("ci95") or {}
        q = _q(r)
        print(f"  {i}. {r['model']:<46} composite={r.get('composite')}  "
              f"qualité={q if q is not None else '—'}/5 "
              f"[{ci.get('low', '—')}–{ci.get('high', '—')}]  "
              f"dispo={r['availability']:.0%}  "
              f"coût/1k={r['cost_usd_per_1k_answers']}$")
    if any(len(g) > 1 for g in groups):
        print("\n  Groupes statistiquement indiscernables (IC 95% chevauchants) :")
        for i, g in enumerate(groups, 1):
            print(f"    G{i} : {', '.join(g)}")

    summary = {
        "suite": "model_benchmark",
        "n_questions": len(cases),
        "repeat": repeat,
        "n_judges": n_judges,
        "retries": retries,
        "weights": _WEIGHTS,
        "paired_question_ids": common,
        "n_paired_questions": len(common),
        "excluded_models": excluded,
        "tie_groups": groups,
        "ranking": [{"model": r["model"],
                     "composite": r.get("composite"),
                     "global_mean": _q(r),
                     "global_mean_all": r["global_mean"],
                     "ci95": paired.get(r["model"], {}).get("ci95") or r["ci95"],
                     "stability_std": r["stability"].get("std"),
                     "hallucination_rate": r["hallucination_rate"],
                     "judge_disagreement": r["judge_disagreement"],
                     "checks_pass_rate": r["checks_pass_rate"],
                     "checks": r["checks"],
                     "ungrounded_flag_rate": r.get("ungrounded_flag_rate"),
                     "availability": r["availability"],
                     "p50_ms": r["latency"].get("p50_ms"),
                     "p95_ms": r["latency"].get("p95_ms"),
                     "cost_usd_per_1k_answers": r["cost_usd_per_1k_answers"],
                     "scores": r["scores"], "errors": r["errors"]}
                    for r in ranking],
        "details": board,
    }
    save_results("model_benchmark", summary)
    if push:
        try:
            from evals.langfuse_sink import push_models
            n = push_models(summary)
            if n:
                print(f"  {n} traces poussées vers Langfuse (tag `eval`)")
        except Exception as e:
            print(f"  Langfuse non poussé : {e}")
    return summary


def _recompute_checks(board: list[dict]) -> None:
    """Rejoue les contrôles déterministes sur les réponses déjà stockées.

    Les checks sont des fonctions pures de (réponse, contexte) : corriger une
    règle ne doit pas coûter 360 appels LLM. C'est tout l'intérêt de garder le
    texte des réponses dans `details`.
    """
    for row in board:
        ok = [a for a in row["answers"] if "error" not in a]
        for a in ok:
            det = run_checks(a["answer"], context=_CONTEXT, out_of_stock=_OUT_OF_STOCK)
            a["checks"], a["flags"] = det["checks"], det["flags"]
            a["checks_details"] = det["details"]
        per_check = {}
        if ok:
            for name in ok[0]["checks"]:
                per_check[name] = round(sum(a["checks"][name] for a in ok) / len(ok), 3)
        row["checks"] = per_check
        row["checks_pass_rate"] = (round(sum(per_check.values()) / len(per_check), 3)
                                   if per_check else None)
        row["ungrounded_flag_rate"] = (
            round(sum(bool(a["flags"]["chiffres_non_ancres"]) for a in ok) / len(ok), 3)
            if ok else None)


def reaggregate() -> dict:
    """Rejoue l'agrégation sur le dernier run sauvegardé (zéro appel LLM)."""
    import json
    path = RESULTS_DIR / "model_benchmark.json"
    with open(path, encoding="utf-8") as f:
        prev = json.load(f)
    _recompute_checks(prev["details"])
    cases = [c for c in load_dataset("coach_qa.json") if c.get("benchmark")]
    ids = {a["id"] for row in prev["details"] for a in row["answers"]}
    cases = [c for c in cases if c["id"] in ids]
    print(f"  Ré-agrégation du run du {prev.get('run_at')} "
          f"({len(prev['details'])} modèles, {len(cases)} questions)")
    return _finalize(prev["details"], cases, repeat=prev.get("repeat", 1),
                     n_judges=prev.get("n_judges", 1), retries=prev.get("retries", 0),
                     push=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=2,
                        help="Passages par question (>1 pour mesurer la stabilité)")
    parser.add_argument("--judges", type=int, default=2,
                        help="Juges distincts par réponse (panel)")
    parser.add_argument("--retries", type=int, default=3,
                        help="Réessais sur 429/5xx (évite le biais de survie)")
    parser.add_argument("--pace", type=float, default=0.4,
                        help="Pause entre deux questions, en secondes")
    parser.add_argument("--only", default="", help="Filtre sur le nom du modèle")
    parser.add_argument("--limit", type=int, default=0,
                        help="N premières questions seulement (rodage rapide)")
    parser.add_argument("--reaggregate", action="store_true",
                        help="Recalcule classement/IC/composite sur le dernier "
                             "run sauvegardé, sans appeler les modèles")
    args = parser.parse_args()

    if args.reaggregate:
        reaggregate()
    else:
        run(repeat=args.repeat, n_judges=args.judges, retries=args.retries,
            pace=args.pace, only=args.only, limit=args.limit)
