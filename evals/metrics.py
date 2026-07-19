"""
metrics.py — Métriques déterministes des bancs d'essai.

Retrieval : hit@k, MRR, nDCG@k, accuracy du flag `relevant`.
Classification (guardrail) : matrice de confusion, précision/rappel/F1 par classe.
Latence : p50/p95/p99.
"""

from __future__ import annotations

import math
from collections import defaultdict


# ══════════════════════════════════════════════════════════════════════════════
# Retrieval
# ══════════════════════════════════════════════════════════════════════════════

def hit_at_k(relevances: list[bool], k: int) -> float:
    """1 si au moins un document pertinent dans le top-k."""
    return 1.0 if any(relevances[:k]) else 0.0


def reciprocal_rank(relevances: list[bool]) -> float:
    for i, rel in enumerate(relevances, 1):
        if rel:
            return 1.0 / i
    return 0.0


def ndcg_at_k(relevances: list[bool], k: int) -> float:
    """nDCG binaire — l'idéal place tous les pertinents en tête."""
    gains = [1.0 if r else 0.0 for r in relevances[:k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    n_rel = min(sum(1 for r in relevances if r), k)
    if n_rel == 0:
        return 0.0
    idcg = sum(1.0 / math.log2(i + 2) for i in range(n_rel))
    return dcg / idcg


# ══════════════════════════════════════════════════════════════════════════════
# Classification multi-classes (statuts guardrail)
# ══════════════════════════════════════════════════════════════════════════════

def classification_report(pairs: list[tuple[str, str]]) -> dict:
    """
    pairs = [(attendu, prédit), ...] →
    accuracy globale + précision/rappel/F1 par classe + matrice de confusion.
    """
    labels = sorted({e for e, _ in pairs} | {p for _, p in pairs})
    confusion: dict[str, dict[str, int]] = {e: defaultdict(int) for e in labels}
    for expected, predicted in pairs:
        confusion[expected][predicted] += 1

    per_class = {}
    for label in labels:
        tp = confusion[label][label]
        fn = sum(confusion[label].values()) - tp
        fp = sum(confusion[other][label] for other in labels if other != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall    = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[label] = {
            "precision": round(precision, 3),
            "recall":    round(recall, 3),
            "f1":        round(f1, 3),
            "support":   tp + fn,
        }

    accuracy = sum(1 for e, p in pairs if e == p) / len(pairs) if pairs else 0.0
    supported = [m for m in per_class.values() if m["support"] > 0]
    macro_f1 = sum(m["f1"] for m in supported) / len(supported) if supported else 0.0

    return {
        "accuracy":  round(accuracy, 3),
        "macro_f1":  round(macro_f1, 3),
        "per_class": per_class,
        "confusion": {e: dict(row) for e, row in confusion.items()},
    }


def binary_prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall    = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn}


# ══════════════════════════════════════════════════════════════════════════════
# Latence
# ══════════════════════════════════════════════════════════════════════════════

def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct / 100
    lo, hi = int(idx), min(int(idx) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


def latency_summary(values_ms: list[float]) -> dict:
    if not values_ms:
        return {"n": 0}
    return {
        "n":       len(values_ms),
        "mean_ms": round(sum(values_ms) / len(values_ms)),
        "p50_ms":  round(percentile(values_ms, 50)),
        "p95_ms":  round(percentile(values_ms, 95)),
        "p99_ms":  round(percentile(values_ms, 99)),
        "max_ms":  round(max(values_ms)),
    }
