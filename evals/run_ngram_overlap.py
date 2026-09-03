"""
Banc BLEU / ROUGE — recouvrement n-gramme entre la réponse générée et la
réponse de référence du jeu RAGAS.

Pourquoi ce banc existe
-----------------------
BLEU (Papineni et al., 2002) et ROUGE (Lin, 2004) sont les métriques de
référence de la traduction automatique et du résumé automatique. Elles mesurent
un *recouvrement de n-grammes* avec un texte de référence, pas une exactitude
factuelle. On les calcule ici pour deux raisons :

1. documenter, chiffres à l'appui, pourquoi elles sont peu informatives pour un
   assistant commercial dont la réponse correcte admet un très grand nombre de
   formulations valides ;
2. disposer malgré tout d'un indicateur de *dérive de style* reproductible et
   sans appel LLM : une chute brutale de ROUGE-L d'une version à l'autre
   signale que le système ne répond plus du tout de la même manière.

Aucune dépendance externe : BLEU-4 (avec brièveté) et ROUGE-1/2/L sont
réimplémentés ici, ce qui rend le banc rejouable hors ligne.

Entrées  : evals/datasets/ragas_qa.json  (champ `reference`)
           evals/results/ragas.json      (champ `answer` par cas)
Sortie   : evals/results/ngram_overlap.json

Usage : python -m evals.run_ngram_overlap
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "datasets" / "ragas_qa.json"
RAGAS = ROOT / "results" / "ragas.json"
OUT = ROOT / "results" / "ngram_overlap.json"

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Minuscule, sans accents ni ponctuation ni balisage Markdown."""
    text = text.replace("*", " ").replace("#", " ").replace("`", " ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return _TOKEN.findall(text.lower())


def ngrams(tokens: list[str], n: int) -> Counter:
    return Counter(tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1))


def modified_precision(cand: list[str], ref: list[str], n: int) -> tuple[int, int]:
    """Numérateur/dénominateur de la précision n-gramme écrêtée (BLEU)."""
    c, r = ngrams(cand, n), ngrams(ref, n)
    if not c:
        return 0, 0
    clipped = sum(min(cnt, r[g]) for g, cnt in c.items())
    return clipped, sum(c.values())


def bleu(cand: list[str], ref: list[str], max_n: int = 4) -> float:
    """BLEU-4 sur une paire candidat/référence, lissage additif +1."""
    if not cand or not ref:
        return 0.0
    logs = []
    for n in range(1, max_n + 1):
        num, den = modified_precision(cand, ref, n)
        if den == 0:
            return 0.0
        logs.append(math.log((num + 1) / (den + 1)))          # lissage
    bp = 1.0 if len(cand) > len(ref) else math.exp(1 - len(ref) / max(1, len(cand)))
    return bp * math.exp(sum(logs) / max_n)


def rouge_n(cand: list[str], ref: list[str], n: int) -> dict:
    c, r = ngrams(cand, n), ngrams(ref, n)
    overlap = sum(min(cnt, r[g]) for g, cnt in c.items())
    p = overlap / sum(c.values()) if c else 0.0
    rec = overlap / sum(r.values()) if r else 0.0
    f = 2 * p * rec / (p + rec) if p + rec else 0.0
    return {"precision": round(p, 4), "recall": round(rec, 4), "f1": round(f, 4)}


def lcs_length(a: list[str], b: list[str]) -> int:
    """Plus longue sous-séquence commune, en O(len(a) x len(b)) mémoire O(len(b))."""
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def rouge_l(cand: list[str], ref: list[str]) -> dict:
    if not cand or not ref:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    lcs = lcs_length(cand, ref)
    p, rec = lcs / len(cand), lcs / len(ref)
    f = 2 * p * rec / (p + rec) if p + rec else 0.0
    return {"precision": round(p, 4), "recall": round(rec, 4), "f1": round(f, 4)}


def main() -> None:
    refs = {c["id"]: c.get("reference", "")
            for c in json.loads(DATASET.read_text(encoding="utf-8"))["cases"]}
    ragas = json.loads(RAGAS.read_text(encoding="utf-8"))

    rows = []
    for det in ragas.get("details", []):
        cid = det.get("id")
        answer, reference = det.get("answer", ""), refs.get(cid, "")
        if not answer or not reference:
            continue
        cand, ref = tokenize(answer), tokenize(reference)
        rows.append({
            "id": cid,
            "domain": det.get("domain"),
            "len_answer": len(cand),
            "len_reference": len(ref),
            "bleu4": round(bleu(cand, ref), 4),
            "rouge1": rouge_n(cand, ref, 1),
            "rouge2": rouge_n(cand, ref, 2),
            "rougeL": rouge_l(cand, ref),
        })

    def avg(path) -> float:
        vals = [path(r) for r in rows]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    out = {
        "suite": "ngram_overlap",
        "source_answers": "evals/results/ragas.json",
        "source_references": "evals/datasets/ragas_qa.json",
        "n_cases": len(rows),
        "means": {
            "bleu4": avg(lambda r: r["bleu4"]),
            "rouge1_f1": avg(lambda r: r["rouge1"]["f1"]),
            "rouge2_f1": avg(lambda r: r["rouge2"]["f1"]),
            "rougeL_f1": avg(lambda r: r["rougeL"]["f1"]),
            "rougeL_recall": avg(lambda r: r["rougeL"]["recall"]),
            "length_ratio": avg(lambda r: r["len_answer"] / max(1, r["len_reference"])),
        },
        "details": rows,
        "run_at": datetime.now().isoformat(timespec="seconds"),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Cas evalues : {len(rows)}")
    for k, v in out["means"].items():
        print(f"  {k:16s} {v}")
    print(f"\nEcrit -> {OUT}")


if __name__ == "__main__":
    main()
