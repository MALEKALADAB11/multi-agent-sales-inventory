"""
check_llm_providers.py — teste chaque provider LLM configuré avec UN appel
minimal, pour savoir en 10 secondes lequel est up / rate-limited / mal
configuré, sans toucher au judge ni au dataset.

Lancer depuis la racine du repo :
    python check_llm_providers.py
"""
import os
import time

from evals.common import load_providers, chat

_PING = [{"role": "user", "content": "Réponds uniquement par le mot OK."}]


def check_one(provider, model: str) -> None:
    t0 = time.perf_counter()
    result = chat(provider, model, _PING, temperature=0.0, max_tokens=10)
    ms = round((time.perf_counter() - t0) * 1000)
    if result.ok:
        print(f"  [OK]        {provider.name:<12} {model:<45} {ms:>5}ms")
    else:
        # Distinguer rate-limit (transitoire) d'une vraie panne (clé invalide, modèle inconnu...)
        kind = "RATE-LIMIT" if "429" in result.error else "ERREUR"
        print(f"  [{kind}]  {provider.name:<12} {model:<45} {ms:>5}ms  {result.error[:120]}")


def main():
    providers = load_providers()

    print("=" * 78)
    print("  DISPONIBILITÉ DES CLÉS (avant tout appel réseau)")
    print("=" * 78)
    for name, p in providers.items():
        status = "configuré" if p.available else "PAS DE CLÉ / PAS DE MODÈLE"
        print(f"  {name:<12} {status:<30} modèles={p.models}")

    print("\n" + "=" * 78)
    print("  APPELS RÉELS (un ping par modèle configuré)")
    print("=" * 78)
    for name, p in providers.items():
        if not p.available:
            print(f"  [SKIP]      {name:<12} — pas de clé, aucun appel tenté")
            continue
        for model in p.models:
            check_one(p, model)

    print("\n" + "=" * 78)
    print("  ORDRE RÉEL UTILISÉ PAR LE JUGE (evals/judge.py::_judge_candidates)")
    print("=" * 78)
    order = [("mistral", 0), ("groq", 0), ("groq", 1), ("openrouter", 1)]
    for pname, idx in order:
        p = providers.get(pname)
        if p and p.available and idx < len(p.models):
            print(f"  -> {pname}/{p.models[idx]}")
        else:
            print(f"  -> {pname}[{idx}]  INDISPONIBLE, sera sauté")


if __name__ == "__main__":
    main()
