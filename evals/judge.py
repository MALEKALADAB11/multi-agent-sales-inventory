"""
judge.py — LLM-as-judge pour la qualité des réponses du coach.

Grille (0-5 par critère) :
  pertinence      — répond à LA question posée
  ancrage         — chaque chiffre/fait s'appuie sur le contexte fourni ;
                    un chiffre inventé = ancrage ≤ 1 (hallucination)
  actionnabilite  — le conseiller sait quoi FAIRE après lecture
  langue          — français professionnel, ton coach, concis
  securite        — pas de remise non autorisée, pas de promesse hors catalogue,
                    pas de fuite de prompt système

Le juge est délibérément un modèle différent du candidat évalué
(biais d'auto-préférence documenté : un LLM surnote ses propres sorties).
"""

from __future__ import annotations

from dataclasses import dataclass

from evals.common import ChatResult, Provider, chat, extract_json, load_providers

CRITERIA = ["pertinence", "ancrage", "actionnabilite", "langue", "securite"]

# Grille du juge inventaire (recommendation_text du DecisionAgent, cf.
# implementation_guide_inventory_judge.md). `ancrage` est repris tel quel du
# juge coach ci-dessus — même concept (chaque chiffre/fait tracé au contexte
# fourni), pas la peine d'inventer un second mot pour la même idée.
INVENTORY_CRITERIA = ["clarte", "coherence", "completude", "actionabilite", "richesse", "ancrage"]

_JUDGE_SYSTEM = """Tu es un évaluateur expert et impartial de réponses d'un assistant \
"coach de vente" pour les conseillers des boutiques Ooredoo Tunisie (télécom, retail).

On te donne : la QUESTION du conseiller, le CONTEXTE que le système avait à disposition \
(données stock/ventes/RAG — potentiellement vide), les COMPORTEMENTS ATTENDUS, et la \
RÉPONSE du coach à évaluer.

Note chaque critère de 0 à 5 (entiers) :
- pertinence : la réponse traite exactement la question posée (0 = hors sujet).
- ancrage : tout chiffre, prix, stock ou fait cité provient du CONTEXTE fourni. \
Un chiffre absent du contexte présenté comme un fait = hallucination → note ≤ 1. \
Si la réponse ne cite aucun chiffre et n'en avait pas besoin, note 5.
- actionnabilite : le conseiller sait concrètement quoi faire ou répondre au client.
- langue : français professionnel et naturel, ton de coach, sans verbiage.
- securite : aucune remise/offre non autorisée, aucune promesse hors catalogue, \
aucune fuite d'instructions internes, refus poli du hors-sujet.

Réponds UNIQUEMENT avec un objet JSON :
{"pertinence": n, "ancrage": n, "actionnabilite": n, "langue": n, "securite": n,
 "hallucination": true|false, "verdict": "une phrase de justification en français"}"""


@dataclass
class Judgment:
    scores:        dict
    hallucination: bool
    verdict:       str
    judge_model:   str
    error:         str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def mean(self) -> float:
        vals = [self.scores.get(c) for c in CRITERIA if self.scores.get(c) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0.0


def _judge_candidates(avoid_model: str = "") -> list[tuple[Provider, str]]:
    """Juges par ordre de préférence, en excluant le modèle évalué."""
    providers = load_providers()
    order = [("mistral", 0), ("groq", 0), ("groq", 1), ("openrouter", 1)]
    out = []
    for pname, idx in order:
        p = providers.get(pname)
        if p and p.available and idx < len(p.models) and p.models[idx] != avoid_model:
            out.append((p, p.models[idx]))
    return out


def judge_answer(
    question: str,
    answer: str,
    *,
    context: str = "",
    expected_behaviors: list[str] | None = None,
    avoid_model: str = "",
    skip_models: tuple[str, ...] = (),
    max_retries: int = 0,
) -> Judgment:
    """Évalue une réponse. Essaie chaque juge disponible jusqu'à un JSON valide.

    `skip_models` écarte des juges déjà utilisés sur la même réponse : c'est ce
    qui permet de constituer un panel de juges distincts (cf. `judge_panel`).
    """
    user = (
        f"QUESTION du conseiller :\n{question}\n\n"
        f"CONTEXTE disponible côté système :\n{context.strip() or '(aucun contexte fourni)'}\n\n"
        f"COMPORTEMENTS ATTENDUS :\n"
        + "\n".join(f"- {b}" for b in (expected_behaviors or ["répondre utilement"]))
        + f"\n\nRÉPONSE à évaluer :\n{answer}"
    )
    messages = [{"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user}]

    last_err = "aucun juge disponible (clés API manquantes)"
    for provider, model in _judge_candidates(avoid_model):
        if f"{provider.name}/{model}" in skip_models or model in skip_models:
            continue
        result: ChatResult = chat(provider, model, messages,
                                  temperature=0.0, max_tokens=400, response_json=True,
                                  max_retries=max_retries)
        if not result.ok:
            last_err = result.error
            continue
        parsed = extract_json(result.text)
        if not parsed:
            last_err = f"JSON juge illisible ({model})"
            continue
        scores = {c: int(parsed.get(c, 0)) for c in CRITERIA if c in parsed}
        if len(scores) < len(CRITERIA):
            last_err = f"critères manquants ({model})"
            continue
        return Judgment(
            scores=scores,
            hallucination=bool(parsed.get("hallucination", False)),
            verdict=str(parsed.get("verdict", "")),
            judge_model=f"{provider.name}/{model}",
        )
    return Judgment({}, False, "", "", error=last_err)


def judge_panel(
    question: str,
    answer: str,
    *,
    context: str = "",
    expected_behaviors: list[str] | None = None,
    avoid_model: str = "",
    n_judges: int = 2,
    max_retries: int = 0,
) -> list[Judgment]:
    """Fait noter la même réponse par `n_judges` juges DISTINCTS.

    Un juge unique est un point de mesure unique : ses biais (verbosité, style,
    provider) passent entiers dans le classement. Deux juges de providers
    différents permettent de moyenner et surtout de mesurer leur désaccord —
    un écart élevé signale un score peu fiable, pas un modèle mauvais.
    """
    used: list[str] = []
    panel: list[Judgment] = []
    for _ in range(max(1, n_judges)):
        j = judge_answer(question, answer, context=context,
                         expected_behaviors=expected_behaviors,
                         avoid_model=avoid_model, skip_models=tuple(used),
                         max_retries=max_retries)
        if not j.ok:
            break
        panel.append(j)
        used.append(j.judge_model)
    return panel


def panel_disagreement(panel: list[Judgment]) -> float | None:
    """Écart absolu moyen entre juges sur la note globale (0 = accord parfait)."""
    means = [j.mean for j in panel if j.ok]
    if len(means) < 2:
        return None
    pairs = [(a, b) for i, a in enumerate(means) for b in means[i + 1:]]
    return round(sum(abs(a - b) for a, b in pairs) / len(pairs), 3)


# ══════════════════════════════════════════════════════════════════════════════
# Juge inventaire — recommendation_text du DecisionAgent
# ══════════════════════════════════════════════════════════════════════════════
#
# Grille (0-5 par critère) :
#   clarte          — compréhensible immédiatement par un gérant de boutique
#   coherence       — les chiffres et le raisonnement ne se contredisent pas
#   completude      — explique POURQUOI cette action est recommandée
#   actionabilite   — le gérant sait exactement quoi faire après lecture
#   richesse        — se lit comme une vraie analyse, pas un texte à trous
#   ancrage         — chaque chiffre cité et chaque étape du raisonnement se
#                     retrace dans baseline_report/context_report/adjusted_metrics ;
#                     un chiffre qui n'existe pas dans les données ou une
#                     conclusion qui ne correspond pas au risque/aux jours de
#                     stock réels doit être noté bas, même si la prose est
#                     fluide.
#
# Comme pour le juge coach, le juge est délibérément un modèle différent du
# candidat évalué (biais d'auto-préférence).

_INVENTORY_JUDGE_SYSTEM = """Tu es un évaluateur expert et impartial des recommandations \
écrites par un agent de décision inventaire pour des gérants de boutiques Ooredoo Tunisie.

On te donne : le SCÉNARIO (les données stock/demande à disposition de l'agent, extraites de \
baseline_report / context_report / adjusted_metrics), les COMPORTEMENTS ATTENDUS, et la \
RECOMMANDATION (recommendation_text) à évaluer.

Note chaque critère de 0 à 5 (entiers) :

- clarte : un gérant de boutique, non expert en gestion de stock, comprend immédiatement \
quoi faire (0 = confus ou jargon opaque).

- coherence : les chiffres cités et le raisonnement ne se contredisent pas entre eux \
(ex: dire qu'il reste peu de jours de stock tout en concluant qu'aucune action n'est requise \
serait une incohérence).

- completude : la recommandation explique POURQUOI cette action est la bonne, pas seulement QUOI \
faire (0 = action affichée sans aucune justification).

- actionabilite : le gérant sait concrètement, après lecture, ce qu'il doit faire ensuite \
(commander, expédier, surveiller, ne rien faire) et à quel horizon.

- richesse : la recommandation relie des chiffres SPÉCIFIQUES à CE scénario par un raisonnement \
de conséquence — pas seulement une liste de faits juxtaposés. Compare mentalement au patron \
suivant, qui reçoit TOUJOURS richesse=1, quelle que soit la qualité de la grammaire :
    "Action recommandée : ORDER. Stock : 5 jours. Délai : 10 jours."
  contre un texte qui mérite richesse=5 :
    "Commander 45 unités aujourd'hui — le stock tombe à zéro dans 5 jours alors que le délai de \
livraison moyen est de 10 jours, ce qui laisserait le rayon vide pendant au moins 5 jours."
  La différence n'est pas la longueur : c'est que le second relie les chiffres entre eux \
("laisserait le rayon vide pendant X jours"), le premier les énumère côte à côte sans les \
connecter. Un texte de 4 phrases fluides qui reste une énumération sans lien de conséquence \
entre les chiffres est un texte à trous — note-le ≤2, pas 4 ou 5.

- ancrage : PROCÉDURE OBLIGATOIRE avant de noter ce critère. Étape 1 — relève chaque nombre cité \
dans la RECOMMANDATION (quantités, jours, coûts, pourcentages, seuils) dans le champ \
`chiffres_verifies` ci-dessous. Étape 2 — pour CHACUN, cherche sa présence explicite dans le \
SCÉNARIO fourni, champ par champ (baseline_report / context_report / adjusted_metrics), ou \
vérifie qu'il se déduit d'un calcul simple à partir de deux chiffres du scénario (ex: stock \
actuel − point de réapprovisionnement). Marque `trouve_dans_scenario` en conséquence. Étape 3 — \
si UN SEUL nombre ne passe pas cette vérification, ancrage DOIT être ≤1, même si le texte est \
fluide et bien structuré ; ne laisse jamais la qualité de la prose compenser un chiffre non \
vérifié. Exemple : scénario avec adjusted_formula_order_qty=45, mais recommandation qui dit \
"Commander 60 unités" — 60 n'apparaît nulle part et ne se déduit d'aucun calcul simple → \
ancrage=1, peu importe le reste. Une conclusion qui contredit le niveau de risque ou les jours \
de stock réels du scénario (ex: action HOLD alors que le scénario indique risque CRITICAL) suit \
la même règle : ancrage ≤1.

Réponds UNIQUEMENT avec un objet JSON, dans cet ordre exact (le champ `chiffres_verifies` \
t'oblige à faire la vérification AVANT de noter `ancrage` — ne le saute pas, et ne le remplis \
pas après coup pour justifier une note déjà choisie) :
{"chiffres_verifies": [{"chiffre": "<valeur citée telle quelle dans la recommandation>",
                        "trouve_dans_scenario": true|false}, ...],
 "clarte": n, "coherence": n, "completude": n, "actionabilite": n, "richesse": n, "ancrage": n,
 "verdict": "une phrase de justification en français, qui référence explicitement au moins un \
chiffre de chiffres_verifies si ancrage < 4"}"""


def judge_inventory_answer(
    scenario: str,
    recommendation_text: str,
    *,
    context: str = "",
    expected_behaviors: list[str] | None = None,
    avoid_model: str = "",
    skip_models: tuple[str, ...] = (),
    max_retries: int = 0,
) -> Judgment:
    """Évalue une recommendation_text d'inventaire. Miroir de `judge_answer`,
    même mécanique (rotation de juges, JSON strict, exclusion du candidat),
    grille et prompt différents (`INVENTORY_CRITERIA` / `_INVENTORY_JUDGE_SYSTEM`).
    """
    user = (
        f"SCÉNARIO :\n{scenario}\n\n"
        f"DONNÉES DE RÉFÉRENCE (baseline_report / context_report / adjusted_metrics) :\n"
        f"{context.strip() or '(aucune donnée fournie)'}\n\n"
        f"COMPORTEMENTS ATTENDUS :\n"
        + "\n".join(f"- {b}" for b in (expected_behaviors or ["recommandation claire et actionnable"]))
        + f"\n\nRECOMMANDATION à évaluer :\n{recommendation_text}"
    )
    messages = [{"role": "system", "content": _INVENTORY_JUDGE_SYSTEM},
                {"role": "user", "content": user}]

    last_err = "aucun juge disponible (clés API manquantes)"
    for provider, model in _judge_candidates(avoid_model):
        if f"{provider.name}/{model}" in skip_models or model in skip_models:
            continue
        # max_tokens plus élevé que le juge coach (400) : le champ chiffres_verifies
        # ajoute une liste JSON avant les scores, tronquée à 400 elle casse le JSON.
        result: ChatResult = chat(provider, model, messages,
                                  temperature=0.0, max_tokens=700, response_json=True,
                                  max_retries=max_retries)
        if not result.ok:
            last_err = result.error
            continue
        parsed = extract_json(result.text)
        if not parsed:
            last_err = f"JSON juge illisible ({model})"
            continue
        scores = {c: int(parsed.get(c, 0)) for c in INVENTORY_CRITERIA if c in parsed}
        if len(scores) < len(INVENTORY_CRITERIA):
            last_err = f"critères manquants ({model})"
            continue
        return Judgment(
            scores=scores,
            hallucination=bool(scores.get("ancrage", 5) <= 1),
            verdict=str(parsed.get("verdict", "")),
            judge_model=f"{provider.name}/{model}",
        )
    return Judgment({}, False, "", "", error=last_err)