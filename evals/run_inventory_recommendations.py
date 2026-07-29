"""
run_inventory_recommendations.py — Éval LLM-as-judge du DecisionAgent inventaire.

Appelle `create_decide_node` directement (pas le serveur, pas la base de
données, pas `create_orchestrator`) : ce dernier va toujours chercher les
données stock/produit réelles en base et n'a aucun moyen d'injecter un²
scénario synthétique. `create_decide_node` prend le state dict qu'on lui
donne et exécute le même prompt de production (`DECIDE_SYSTEM`/`DECIDE_USER`)
sans dépendance externe — c'est ce qui permet des scénarios figés et
reproductibles (cf. implementation_guide_inventory_judge.md).

Pour chaque cas du dataset :
  1. construit le state (baseline_report / context_report / adjusted_metrics)
  2. appelle decide_node(state) → recommendation_text réel (chemin LLM prod)
  3. juge la recommandation avec judge_inventory_answer (6 critères 0-5)
  4. si `compare_fallback: true` : appelle aussi le chemin rule-based
     (use_llm=False) et juge ce texte-là aussi, pour vérifier que `richesse`
     discrimine bien un texte templaté d'une vraie analyse

    python -m evals.run_inventory_recommendations [--no-judge]

Avant de faire confiance aux scores agrégés (cf. guide, section "Testing the
eval itself, before trusting its scores"), trois vérifications séparées :

    --dry-run N            imprime le texte complet + le JSON complet du juge
                            pour les N premiers cas — attrape les bugs de
                            câblage (mauvaise clé de state, contexte vide)
                            qu'un score bas silencieux ferait passer pour un
                            problème de qualité du candidat
    --determinism-check    rejoue le même texte 2x sur le même juge
                            (température déjà à 0) — les scores doivent
                            matcher ou être à ±1 point ; sinon le prompt du
                            juge est sous-spécifié
    --sanity-check         vérifie que richesse et ancrage discriminent
                            vraiment (fallback rule-based / chiffre corrompu)

Aucun serveur requis — seules des clés API LLM sont nécessaires.
"""

from __future__ import annotations

import argparse
import json
import re

from evals.common import load_dataset, save_results
from evals.judge import INVENTORY_CRITERIA, judge_inventory_answer, judge_roster

# Statuts des contrôles du juge. `NON_VERIFIE` existe pour une raison précise :
# un contrôle qui n'a pas tourné rendait `None`, et `report.py` imprimait alors
# une section vide — impossible à distinguer d'un « rien à signaler ». Un
# contrôle absent doit être aussi visible qu'un contrôle échoué, sinon la seule
# garantie que le juge discrimine vraiment disparaît sans bruit du rapport.
OK, ECHEC, NON_VERIFIE = "OK", "ECHEC", "NON_VERIFIE"


def _not_verified(reason: str) -> dict:
    return {"status": NON_VERIFIE, "reason": reason}


# ══════════════════════════════════════════════════════════════════════════════
# Construction du decide_node (même chemin que la production)
# ══════════════════════════════════════════════════════════════════════════════

def _build_decide_nodes():
    """Retourne (decide_node_llm, decide_node_rule_based).

    decide_node_llm      : create_decide_node(llm=get_smart_llm(), use_llm=True)
    decide_node_rule_based : create_decide_node(llm=None, use_llm=False)
                             — utilisé uniquement pour les cas `compare_fallback`
    """
    from app.inventory.agents.decision.nodes import create_decide_node
    from app.inventory.utils.llm_factory import get_smart_llm  # même helper que supervisor.py

    llm = get_smart_llm()
    decide_llm = create_decide_node(llm=llm, use_llm=True)
    decide_rule_based = create_decide_node(llm=None, use_llm=False)
    return decide_llm, decide_rule_based


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def build_state(case: dict) -> dict:
    """State dict attendu par decide_node — clé `baseline_report`, pas `analysis_report`."""
    return {
        "sku": case.get("sku", "TEST"),
        "store_id": case.get("store_id", "TEST"),
        "business_objective": case.get("business_objective", "standard"),
        "baseline_report": case["baseline_report"],
        "context_report": case["context_report"],
        "adjusted_metrics": case["adjusted_metrics"],
    }


def build_context_string(case: dict) -> str:
    """Contexte de référence pour le juge — les données que l'agent avait à disposition."""
    parts = [
        f"sku: {case.get('sku', 'TEST')}  |  store_id: {case.get('store_id', 'TEST')}",
        "baseline_report: " + json.dumps(case["baseline_report"], ensure_ascii=False),
        "context_report: " + json.dumps(case["context_report"], ensure_ascii=False),
        "adjusted_metrics: " + json.dumps(case["adjusted_metrics"], ensure_ascii=False),
        f"business_objective: {case.get('business_objective', 'standard')}",
    ]
    return "\n".join(parts)


def _corrupt_one_number(text: str) -> str | None:
    """Remplace le premier nombre significatif (>=2 chiffres) du texte par une
    valeur incohérente — utilisé pour vérifier que `ancrage` discrimine bien
    un chiffre halluciné d'un chiffre réel. Retourne None si aucun nombre trouvé."""
    m = re.search(r"\b\d{2,}\b", text)
    if not m:
        return None
    original = int(m.group(0))
    corrupted = original * 3 + 17  # valeur qui ne correspond à aucun champ du scénario
    return text[:m.start()] + str(corrupted) + text[m.end():]


# ══════════════════════════════════════════════════════════════════════════════
# Sanity checks du juge (cf. guide, section "Testing the eval itself")
# ══════════════════════════════════════════════════════════════════════════════

def run_sanity_checks(cases: list[dict], decide_llm, decide_rule_based) -> dict:
    """Vérifie que le juge discrimine réellement avant de faire confiance aux scores :
      - richesse : le fallback rule-based doit scorer visiblement plus bas que le LLM
      - ancrage  : un chiffre corrompu dans une vraie sortie LLM doit faire chuter la note
    """
    print("\n" + "=" * 62)
    print("  SANITY CHECKS — le juge discrimine-t-il vraiment ?")
    print("=" * 62)

    results = {
        "richesse": _not_verified("contrôle non exécuté"),
        "ancrage":  _not_verified("contrôle non exécuté"),
        "judge_roster": judge_roster(),
    }

    # ── richesse : LLM vs rule-based sur un cas fallback_anchor ─────────────
    anchor_cases = [c for c in cases if c.get("compare_fallback")]
    if not anchor_cases:
        results["richesse"] = _not_verified("aucun cas `compare_fallback` dans le dataset")
    if anchor_cases:
        case = anchor_cases[0]
        state = build_state(case)
        context = build_context_string(case)

        llm_result = decide_llm(state)
        llm_text = llm_result["decision"]["recommendation_text"]
        rb_result = decide_rule_based(state)
        rb_text = rb_result["decision"]["recommendation_text"]

        j_llm = judge_inventory_answer(case["scenario"], llm_text, context=context,
                                       expected_behaviors=case.get("expected_behaviors"))
        j_rb = judge_inventory_answer(case["scenario"], rb_text, context=context,
                                      expected_behaviors=case.get("expected_behaviors"),
                                      skip_models=(j_llm.judge_model,) if j_llm.ok else ())

        if j_llm.ok and j_rb.ok:
            r_llm = j_llm.scores.get("richesse")
            r_rb = j_rb.scores.get("richesse")
            discriminates = (r_llm is not None and r_rb is not None and r_llm > r_rb)
            results["richesse"] = {
                "status": OK if discriminates else ECHEC,
                "case_id": case["id"], "llm_score": r_llm, "rule_based_score": r_rb,
                "discriminates": discriminates,
                "judges": [j_llm.judge_model, j_rb.judge_model],
            }
            print(f"  [{OK if discriminates else ECHEC}] richesse — "
                  f"LLM={r_llm} vs rule-based={r_rb} (cas: {case['id']})")
            if not discriminates:
                print("    → le juge ne distingue pas un texte templaté d'une vraie analyse ;"
                      " affiner _INVENTORY_JUDGE_SYSTEM avec un exemple concret de texte 'à trous'.")
        else:
            reason = f"juge indisponible ({j_llm.error or j_rb.error})"
            results["richesse"] = _not_verified(reason)
            print(f"  [{NON_VERIFIE}] richesse — {reason}")

    # ── ancrage : corruption manuelle d'un chiffre dans une vraie sortie ────
    plain_cases = [c for c in cases if not c.get("compare_fallback")]
    if not plain_cases:
        results["ancrage"] = _not_verified("aucun cas hors `compare_fallback` dans le dataset")
    if plain_cases:
        case = plain_cases[0]
        state = build_state(case)
        context = build_context_string(case)

        llm_result = decide_llm(state)
        original_text = llm_result["decision"]["recommendation_text"]
        corrupted_text = _corrupt_one_number(original_text)

        if corrupted_text is None:
            results["ancrage"] = _not_verified(
                "aucun nombre à corrompre dans la sortie LLM")
            print(f"  [{NON_VERIFIE}] ancrage — aucun nombre trouvé à corrompre dans la sortie LLM")
        else:
            j_orig = judge_inventory_answer(case["scenario"], original_text, context=context,
                                            expected_behaviors=case.get("expected_behaviors"))
            j_corrupt = judge_inventory_answer(case["scenario"], corrupted_text, context=context,
                                               expected_behaviors=case.get("expected_behaviors"),
                                               skip_models=(j_orig.judge_model,) if j_orig.ok else ())
            if j_orig.ok and j_corrupt.ok:
                a_orig = j_orig.scores.get("ancrage")
                a_corrupt = j_corrupt.scores.get("ancrage")
                discriminates = (a_orig is not None and a_corrupt is not None and a_orig > a_corrupt)
                results["ancrage"] = {
                    "status": OK if discriminates else ECHEC,
                    "case_id": case["id"], "original_score": a_orig, "corrupted_score": a_corrupt,
                    "discriminates": discriminates,
                    "judges": [j_orig.judge_model, j_corrupt.judge_model],
                }
                print(f"  [{OK if discriminates else ECHEC}] ancrage — "
                      f"original={a_orig} vs corrompu={a_corrupt} (cas: {case['id']})")
                if not discriminates:
                    print("    → le juge n'a pas repéré le chiffre incohérent ; ajouter une"
                          " instruction explicite de recoupement chiffre par chiffre.")
            else:
                reason = f"juge indisponible ({j_orig.error or j_corrupt.error})"
                results["ancrage"] = _not_verified(reason)
                print(f"  [{NON_VERIFIE}] ancrage — {reason}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 1. Dry run — inspection manuelle avant de faire tourner tout le dataset
# ══════════════════════════════════════════════════════════════════════════════

def dry_run(cases: list[dict], decide_llm, n: int = 2) -> None:
    """Affiche le texte complet + le JSON complet du juge pour les `n`
    premiers cas (un critical_stockout et un hold_overstock si possible),
    pour lecture humaine avant de lancer tout le dataset. Attrape les bugs
    de câblage (mauvaise clé de state, build_context_string vide/cassé) que
    des scores bas silencieux feraient sinon passer pour un problème de
    qualité du candidat.
    """
    # Priorité à un cas de chaque grande famille si dispo, sinon les n premiers.
    preferred = []
    for cat in ("critical_stockout", "hold_overstock"):
        match = next((c for c in cases if c["category"] == cat), None)
        if match:
            preferred.append(match)
    sample = (preferred + [c for c in cases if c not in preferred])[:n]

    print("\n" + "=" * 62)
    print(f"  DRY RUN — {len(sample)} cas, lecture humaine")
    print("=" * 62)

    for case in sample:
        state = build_state(case)
        context = build_context_string(case)

        print(f"\n--- {case['id']} ({case['category']}) ---")
        print(f"Scénario : {case['scenario']}")

        try:
            result = decide_llm(state)
            decision = result["decision"]
        except Exception as e:
            print(f"[ERREUR decide_node] {type(e).__name__}: {e}")
            continue

        print(f"\naction={decision.get('action')} qty={decision.get('order_qty')} "
              f"urgency={decision.get('urgency')} confidence={decision.get('confidence')} "
              f"source={decision.get('reasoning_source')}")
        print(f"\nrecommendation_text :\n{decision.get('recommendation_text', '(vide)')}")

        j = judge_inventory_answer(case["scenario"], decision.get("recommendation_text", ""),
                                   context=context, expected_behaviors=case.get("expected_behaviors"))
        if j.ok:
            print(f"\njuge ({j.judge_model}) :")
            print(json.dumps({**j.scores, "verdict": j.verdict}, ensure_ascii=False, indent=2))
        else:
            print(f"\n[juge indisponible] {j.error}")

    print("\n" + "=" * 62)
    print("  Fin du dry run — relire les textes ci-dessus avant de lancer le dataset complet.")
    print("=" * 62)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Déterminisme du juge — la même réponse doit produire des scores stables
# ══════════════════════════════════════════════════════════════════════════════

def check_judge_determinism(case: dict, recommendation_text: str, context: str,
                            n_repeats: int = 2) -> dict:
    """Rejoue judge_inventory_answer sur EXACTEMENT le même texte `n_repeats`
    fois. La grille tourne déjà à température 0 (cf. `chat()` dans judge.py),
    donc les scores devraient matcher ou rester à ±1 point d'un appel à
    l'autre. Un écart plus large signale un prompt de juge sous-spécifié —
    pas un problème avec le candidat évalué.

    Note : chaque appel peut retomber sur un juge différent si le premier
    juge de la rotation est indisponible (cf. `_judge_candidates`) ; le
    `judge_model` de chaque essai est rapporté pour distinguer ce cas d'une
    vraie instabilité du même juge.
    """
    # max_retries : un 429 passager sur un seul des essais suffisait à faire
    # tomber ok_runs sous 2 et à rendre le contrôle « non vérifié » — ce qui
    # s'est produit au run du 26/07 alors que quatre juges étaient configurés.
    # Le contrôle n'a que deux appels à faire, autant les faire aboutir.
    runs = []
    for _ in range(max(2, n_repeats)):
        j = judge_inventory_answer(case["scenario"], recommendation_text, context=context,
                                   expected_behaviors=case.get("expected_behaviors"),
                                   max_retries=2)
        runs.append(j)

    ok_runs = [j for j in runs if j.ok]
    if len(ok_runs) < 2:
        roster = judge_roster()
        return {**_not_verified(
                    f"{len(ok_runs)} essai(s) abouti(s) sur {len(runs)} — "
                    f"juges configurés : {', '.join(roster) or 'aucun'}"),
                "case_id": case["id"], "stable": None, "judge_roster": roster}

    max_spread = {
        c: max(j.scores.get(c, 0) for j in ok_runs) - min(j.scores.get(c, 0) for j in ok_runs)
        for c in INVENTORY_CRITERIA
    }
    stable = all(spread <= 1 for spread in max_spread.values())
    same_judge = len({j.judge_model for j in ok_runs}) == 1

    result = {
        "status": OK if stable else ECHEC,
        "case_id": case["id"],
        "stable": stable,
        "same_judge_each_run": same_judge,
        "max_spread_by_criterion": max_spread,
        "runs": [{**j.scores, "judge_model": j.judge_model} for j in ok_runs],
    }

    print(f"  [{OK if stable else ECHEC}] déterminisme — {case['id']} — écart max par critère: {max_spread}"
          f"{'' if same_judge else '  (juges différents entre essais — comparaison moins fiable)'}")
    if not stable:
        print("    → le prompt du juge est sous-spécifié ; ajouter des exemples concrets"
              " de ce à quoi ressemble chaque niveau de note.")

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Run principal
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(summary: dict) -> None:
    print("\n" + "=" * 62)
    print(f"  INVENTORY RECOMMENDATIONS — {summary['n_success']}/{summary['n_cases']} évaluées")
    print("=" * 62)
    if summary.get("judge_scores_mean"):
        print("  juge (0-5)      " + "  ".join(
            f"{c}={summary['judge_scores_mean'][c]}" for c in INVENTORY_CRITERIA))
        print(f"  score global    {summary['judge_global_mean']}/5")
    if summary.get("fallback_comparison"):
        for comp in summary["fallback_comparison"]:
            print(f"  richesse (fallback) {comp['case_id']}: LLM={comp['llm_richesse']} "
                  f"vs rule-based={comp['rule_based_richesse']}")
    health = summary.get("judge_health") or {}
    if health.get("n_distinct", 0) < 2:
        print(f"  ATTENTION — {health.get('n_distinct', 0)} juge configuré "
              f"({', '.join(health.get('roster') or []) or 'aucun'}) : ni panel ni "
              "contrôle de déterminisme croisé possibles, le biais de ce juge unique "
              "traverse tous les scores ci-dessus.")


def run(use_judge: bool = True, run_sanity: bool = False, run_determinism: bool = False) -> dict:
    cases = load_dataset("inventory_recommendations.json")
    decide_llm, decide_rule_based = _build_decide_nodes()

    rows: list[dict] = []
    judged: list[dict] = []
    fallback_comparison: list[dict] = []

    for case in cases:
        state = build_state(case)
        context = build_context_string(case)

        try:
            result = decide_llm(state)
            decision = result["decision"]
            recommendation_text = decision.get("recommendation_text", "")
            error = "" if recommendation_text else "recommendation_text vide"
        except Exception as e:
            decision, recommendation_text = {}, ""
            error = f"{type(e).__name__}: {e}"

        row = {
            "id": case["id"],
            "category": case["category"],
            "scenario": case["scenario"],
            "recommendation_text": recommendation_text,
            "action": decision.get("action"),
            "reasoning_source": decision.get("reasoning_source"),
            "error": error,
            "judge": None,
        }

        if recommendation_text and use_judge:
            j = judge_inventory_answer(
                case["scenario"], recommendation_text, context=context,
                expected_behaviors=case.get("expected_behaviors"),
            )
            if j.ok:
                row["judge"] = {**j.scores, "mean": j.mean, "verdict": j.verdict,
                                "judge_model": j.judge_model}
                judged.append(row["judge"])
            else:
                row["judge"] = {"error": j.error}

        if recommendation_text and case.get("compare_fallback"):
            try:
                rb_result = decide_rule_based(state)
                rb_text = rb_result["decision"].get("recommendation_text", "")
                if rb_text and use_judge:
                    j_rb = judge_inventory_answer(
                        case["scenario"], rb_text, context=context,
                        expected_behaviors=case.get("expected_behaviors"),
                    )
                    if j_rb.ok and row.get("judge") and "richesse" in row["judge"]:
                        fallback_comparison.append({
                            "case_id": case["id"],
                            "llm_richesse": row["judge"]["richesse"],
                            "rule_based_richesse": j_rb.scores.get("richesse"),
                        })
            except Exception as e:
                print(f"  [WARN] fallback comparison failed for {case['id']}: {e}")

        status = "OK" if not row["error"] else "ERR"
        score = f" juge={row['judge']['mean']:.1f}" if row.get("judge") and "mean" in (row["judge"] or {}) else ""
        print(f"  [{status}] {case['id']:<22} action={row['action']}{score}")
        rows.append(row)

    n_ok = sum(1 for r in rows if not r["error"])
    mean_by_criterion = {
        c: round(sum(j[c] for j in judged) / len(judged), 2) if judged else None
        for c in INVENTORY_CRITERIA
    }

    roster = judge_roster()
    summary = {
        "suite": "inventory_recommendations",
        "n_cases": len(cases),
        "n_success": n_ok,
        "success_rate": round(n_ok / len(cases), 3) if cases else 0.0,
        "judge_scores_mean": mean_by_criterion,
        "judge_global_mean": round(sum(j["mean"] for j in judged) / len(judged), 2) if judged else None,
        "fallback_comparison": fallback_comparison,
        # Un seul juge configuré = pas de panel, pas de contrôle de déterminisme
        # croisé, et un biais de juge unique qui traverse tous les scores sans
        # que rien ne le signale. Publié à côté des notes, pas en note de bas de page.
        "judge_health": {"roster": roster, "n_distinct": len(roster)},
        # Toujours présents, même non demandés : c'est ce qui distingue « le juge
        # a été validé » de « personne n'a regardé ».
        "sanity_checks": {
            "richesse": _not_verified("non demandé (relancer avec --sanity-check)"),
            "ancrage":  _not_verified("non demandé (relancer avec --sanity-check)"),
        },
        "determinism_check": _not_verified("non demandé (relancer avec --determinism-check)"),
        "details": rows,
    }

    print_summary(summary)

    if run_sanity:
        summary["sanity_checks"] = run_sanity_checks(cases, decide_llm, decide_rule_based)

    if run_determinism:
        print("\n" + "=" * 62)
        print("  DÉTERMINISME DU JUGE — même texte, appels répétés")
        print("=" * 62)
        first_scored = next((r for r in rows if r.get("recommendation_text")
                             and r.get("judge") and "mean" in (r["judge"] or {})), None)
        if first_scored:
            case = next(c for c in cases if c["id"] == first_scored["id"])
            summary["determinism_check"] = check_judge_determinism(
                case, first_scored["recommendation_text"], build_context_string(case),
            )
        else:
            summary["determinism_check"] = _not_verified(
                "aucun cas jugé avec succès à rejouer")
            print(f"  [{NON_VERIFIE}] aucun cas jugé avec succès à rejouer")

    save_results("inventory_recommendations", summary)
    try:
        from evals.langfuse_sink import push_inventory_recommendations
        n = push_inventory_recommendations(summary)
        if n:
            print(f"  {n} traces poussées vers Langfuse (tag `eval`)")
    except Exception as e:
        print(f"  Langfuse non poussé : {e}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--dry-run", type=int, default=0, metavar="N",
                        help="N'exécute que les N premiers cas, imprime le texte complet "
                             "et le JSON complet du juge pour lecture humaine, ne sauvegarde "
                             "rien. À faire avant un premier run complet.")
    parser.add_argument("--determinism-check", action="store_true",
                        help="Rejoue le juge 2x sur le même texte pour vérifier la stabilité "
                             "des scores (± 1 point) avant de faire confiance aux agrégats.")
    parser.add_argument("--sanity-check", action="store_true",
                        help="Vérifie que le juge discrimine richesse/ancrage avant de faire confiance aux scores.")
    args = parser.parse_args()

    import sys

    if args.dry_run:
        _cases = load_dataset("inventory_recommendations.json")
        _decide_llm, _ = _build_decide_nodes()
        dry_run(_cases, _decide_llm, n=args.dry_run)
        sys.exit(0)

    s = run(use_judge=not args.no_judge, run_sanity=args.sanity_check,
            run_determinism=args.determinism_check)
    sys.exit(0 if s["success_rate"] >= 0.9 else 1)