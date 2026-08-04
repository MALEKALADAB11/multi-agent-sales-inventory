"""
figures.py — Génération des figures du chapitre 2 (Compréhension métier).

Palette : instance de référence validée (voir dataviz/references/palette.md).
Sortie  : docs/rapport/img/*.png  (200 dpi, fond clair, sans titre incrusté —
          le titre est porté par la légende LaTeX).

Usage : python docs/rapport/figures.py
"""
from __future__ import annotations

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Ellipse, FancyArrowPatch, Circle

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img")
os.makedirs(OUT, exist_ok=True)

# ── Palette (mode clair) ─────────────────────────────────────────────────────
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
S5, S6, S7, S8 = "#e87ba4", "#008300", "#4a3aa7", "#e34948"
GOOD, WARN, SERIOUS, CRIT = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
SURFACE = "#fcfcfb"
INK, INK2, INK3 = "#0b0b0b", "#52514e", "#8a8880"
GRID = "#e3e2dd"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK2,
    "text.color": INK,
    "xtick.color": INK2,
    "ytick.color": INK2,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})


def save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=200, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    print("  ->", name)


def box(ax, x, y, w, h, text, face="#ffffff", edge=S1, lw=1.4, fs=9,
        weight="normal", tc=INK, radius=0.02, ls="-"):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle=f"round,pad=0,rounding_size={radius}",
                                facecolor=face, edgecolor=edge, linewidth=lw,
                                linestyle=ls, zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color=tc, weight=weight, zorder=3, linespacing=1.45)


def arrow(ax, p1, p2, color=INK2, lw=1.3, style="-|>", ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=13,
                                 color=color, linewidth=lw, linestyle=ls,
                                 connectionstyle=f"arc3,rad={rad}",
                                 shrinkA=2, shrinkB=2, zorder=4))


def blank(ax, xlim=(0, 10), ylim=(0, 6)):
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.axis("off")


# ═════════════════════════════════════════════════════════════════════════════
# 1. Cycle CRISP-DM, phase 1 mise en évidence
# ═════════════════════════════════════════════════════════════════════════════
def fig_crispdm_phase1():
    fig, ax = plt.subplots(figsize=(8.2, 6.2))
    blank(ax, (0, 10), (0, 7.6))

    phases = [
        ("1. Compréhension\nmétier", 5.0, 6.35, True),
        ("2. Compréhension\ndes données", 7.9, 4.75, False),
        ("3. Préparation\ndes données", 7.9, 2.45, False),
        ("4. Modélisation", 5.0, 0.85, False),
        ("5. Évaluation", 2.1, 2.45, False),
        ("6. Déploiement", 2.1, 4.75, False),
    ]
    ax.add_patch(Circle((5.0, 3.6), 1.28, facecolor="#f2f6fd",
                        edgecolor=GRID, linewidth=1.2, zorder=1))
    ax.text(5.0, 3.75, "DONNÉES", ha="center", va="center", fontsize=9.5,
            weight="bold", color=S1)
    ax.text(5.0, 3.30, "Retail\nOoredoo", ha="center", va="center",
            fontsize=8, color=INK2)

    for label, cx, cy, hi in phases:
        face = S1 if hi else "#ffffff"
        tc = "#ffffff" if hi else INK
        edge = S1 if hi else GRID
        w, h = 2.35, 0.95
        box(ax, cx - w / 2, cy - h / 2, w, h, label, face=face, edge=edge,
            lw=1.9 if hi else 1.2, fs=8.8, weight="bold" if hi else "normal",
            tc=tc, radius=0.08)

    def link(a, b, color=INK3, lw=1.4, ls="-", frac=0.34):
        _, x1, y1, _ = phases[a]
        _, x2, y2, _ = phases[b]
        dx, dy = x2 - x1, y2 - y1
        n = (dx ** 2 + dy ** 2) ** 0.5
        off = n * frac
        arrow(ax, (x1 + dx / n * off, y1 + dy / n * off),
              (x2 - dx / n * off, y2 - dy / n * off),
              color=color, lw=lw, ls=ls, rad=-0.22)

    for a, b in [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0)]:
        link(a, b)

    link(3, 2, color=S2, lw=1.3, ls=(0, (4, 3)))
    ax.text(8.05, 1.35, "retours\nitératifs", fontsize=7.6, color=S2,
            ha="left", va="center", style="italic")

    ax.text(5.0, 7.35, "Phase traitée dans ce chapitre", ha="center",
            fontsize=8.6, color=S1, weight="bold")
    save(fig, "fig_crispdm_phase1.png")


# ═════════════════════════════════════════════════════════════════════════════
# 2. Les quatre tâches de la phase de compréhension métier
# ═════════════════════════════════════════════════════════════════════════════
def fig_taches_phase1():
    fig, ax = plt.subplots(figsize=(12.4, 5.0))
    blank(ax, (0, 24), (0, 9.6))

    taches = [
        ("Déterminer les\nobjectifs métier",
         "• Contexte métier\n• Objectifs OM-1 à OM-7\n• Critères de succès\n   métier CSM-1 à CSM-6", S1),
        ("Évaluer la\nsituation",
         "• Inventaire des ressources\n• Acteurs et exigences\n• Contraintes et risques\n• Terminologie · coûts", S2),
        ("Déterminer les objectifs\nde fouille de données",
         "• Objectifs OD-1 à OD-8\n• Critères de succès\n   techniques\n• Matrice de traçabilité", S3),
        ("Produire le plan\nde projet",
         "• Backlog priorisé MoSCoW\n• 12 itérations\n• Évaluation initiale des\n   outils et techniques", S7),
    ]
    w, gap = 5.1, 0.95
    for i, (titre, contenu, col) in enumerate(taches):
        x = 0.35 + i * (w + gap)
        ax.add_patch(FancyBboxPatch((x, 1.0), w, 7.3,
                                    boxstyle="round,pad=0,rounding_size=0.12",
                                    facecolor="#ffffff", edgecolor=GRID,
                                    linewidth=1.2, zorder=2))
        ax.add_patch(Rectangle((x, 7.0), w, 1.3, facecolor=col, edgecolor="none",
                               zorder=3))
        ax.text(x + w / 2, 7.65, titre, ha="center", va="center", fontsize=9.4,
                weight="bold", color="#ffffff", zorder=4, linespacing=1.35)
        ax.text(x + 0.35, 6.55, contenu, ha="left", va="top", fontsize=8.6,
                color=INK2, zorder=4, linespacing=1.75)
        ax.text(x + 0.35, 1.45, f"Tâche {i + 1}", ha="left", va="bottom",
                fontsize=8, color=col, weight="bold", zorder=4)
        if i < 3:
            arrow(ax, (x + w + 0.12, 4.6), (x + w + gap - 0.12, 4.6),
                  color=INK3, lw=1.5)
    save(fig, "fig_taches_phase1.png")


# ═════════════════════════════════════════════════════════════════════════════
# 3. Processus décisionnel actuel en boutique (AS-IS) et points de rupture
# ═════════════════════════════════════════════════════════════════════════════
def fig_processus_asis():
    fig, ax = plt.subplots(figsize=(12.4, 5.6))
    blank(ax, (0, 24), (0, 11))

    etapes = [
        "Ouverture\nde la boutique",
        "Objectif du jour\ncommuniqué",
        "Ventes au fil\nde la journée",
        "Constat de\nl'écart",
        "Clôture et\nreporting",
    ]
    w, gap = 3.9, 1.15
    xs = []
    for i, e in enumerate(etapes):
        x = 0.6 + i * (w + gap)
        xs.append(x)
        box(ax, x, 6.4, w, 1.9, e, face="#ffffff", edge=S1, lw=1.4, fs=9,
            radius=0.1)
        if i < len(etapes) - 1:
            arrow(ax, (x + w + 0.1, 7.35), (x + w + gap - 0.1, 7.35),
                  color=INK3, lw=1.5)

    ruptures = [
        (1, "Objectif fixé sans\nprise en compte du\ncontexte du jour"),
        (2, "Décision du conseiller\nfondée sur l'expérience,\nsans vue du stock"),
        (3, "Écart constaté trop tard\npour être rattrapé"),
        (4, "Retour terrain\nnon capitalisé"),
    ]
    for idx, txt in ruptures:
        x = xs[idx] + w / 2
        arrow(ax, (x, 6.3), (x, 4.75), color=CRIT, lw=1.3, ls=(0, (3, 2)))
        box(ax, x - w / 2 - 0.15, 2.35, w + 0.3, 2.35, txt, face="#fdf1f1",
            edge=CRIT, lw=1.2, fs=8.4, tc=INK2, radius=0.1)

    ax.text(0.6, 10.2, "Processus actuel", fontsize=10, weight="bold", color=S1)
    ax.text(0.6, 1.55, "Points de rupture identifiés", fontsize=10,
            weight="bold", color=CRIT)
    save(fig, "fig_processus_asis.png")


# ═════════════════════════════════════════════════════════════════════════════
# 4. Couplage des deux leviers : commercial et logistique
# ═════════════════════════════════════════════════════════════════════════════
def fig_couplage_domaines():
    fig, ax = plt.subplots(figsize=(9.8, 5.2))
    blank(ax, (0, 20), (0, 10.6))

    box(ax, 0.6, 5.9, 7.6, 3.9,
        "LEVIER COMMERCIAL\n\nQuel produit proposer ?\nQuel argumentaire ?\nÀ quel moment ?",
        face="#f2f6fd", edge=S1, lw=1.7, fs=9.2, radius=0.1)
    box(ax, 11.8, 5.9, 7.6, 3.9,
        "LEVIER LOGISTIQUE\n\nQuelle référence commander ?\nEn quelle quantité ?\nAvec quelle urgence ?",
        face="#fdf3ee", edge=S2, lw=1.7, fs=9.2, radius=0.1)

    arrow(ax, (8.5, 8.4), (11.5, 8.4), color=INK2, lw=1.6)
    arrow(ax, (11.5, 7.0), (8.5, 7.0), color=INK2, lw=1.6)
    ax.text(10.0, 8.8, "pousse la demande", fontsize=8.2, color=INK2, ha="center")
    ax.text(10.0, 6.35, "contraint l'offre", fontsize=8.2, color=INK2, ha="center")

    box(ax, 0.6, 2.6, 7.6, 2.2,
        "Pousser une référence\nen rupture imminente\n= action contre-productive",
        face="#fdf1f1", edge=CRIT, lw=1.3, fs=8.6, tc=INK2, radius=0.1)
    box(ax, 11.8, 2.6, 7.6, 2.2,
        "Commander sans lire la\ndynamique commerciale\n= trésorerie immobilisée",
        face="#fdf1f1", edge=CRIT, lw=1.3, fs=8.6, tc=INK2, radius=0.1)

    box(ax, 5.4, 0.15, 9.2, 1.65,
        "La valeur naît du croisement des deux leviers, non de leur traitement isolé",
        face=S3, edge=S3, lw=1.4, fs=9.4, weight="bold", tc="#ffffff", radius=0.1)
    save(fig, "fig_couplage_domaines.png")


# ═════════════════════════════════════════════════════════════════════════════
# 5. Cartographie des acteurs et frontière de l'automatisation
# ═════════════════════════════════════════════════════════════════════════════
def fig_cartographie_acteurs():
    fig, ax = plt.subplots(figsize=(12.0, 6.4))
    blank(ax, (0, 24), (0, 12.6))

    ax.add_patch(Rectangle((0.4, 0.5), 10.6, 11.4, facecolor="#f6f9fe",
                           edgecolor=S1, linewidth=1.3, zorder=1))
    ax.text(5.7, 11.35, "AGENTS LOGICIELS — analysent et proposent",
            ha="center", fontsize=9.4, weight="bold", color=S1, zorder=3)

    ax.add_patch(Rectangle((13.0, 0.5), 10.6, 11.4, facecolor="#fdf7f3",
                           edgecolor=S2, linewidth=1.3, zorder=1))
    ax.text(18.3, 11.35, "ACTEURS HUMAINS — décident",
            ha="center", fontsize=9.4, weight="bold", color=S2, zorder=3)

    box(ax, 2.9, 9.0, 5.9, 1.5, "Agent Superviseur\n(orchestrateur)",
        face="#ffffff", edge=S1, lw=1.6, fs=8.8, weight="bold", radius=0.1)

    ventes = ["Agent Analyste", "Agent Stratège", "Agent Coach"]
    stocks = ["Agent Analyse\nInventaire", "Agent Contexte\nInventaire",
              "Agent Décision\nInventaire"]
    for i, (v, s) in enumerate(zip(ventes, stocks)):
        y = 6.95 - i * 1.8
        box(ax, 0.9, y, 4.6, 1.45, v, face="#ffffff", edge=S1, lw=1.1, fs=8.4,
            radius=0.1)
        box(ax, 6.0, y, 4.6, 1.45, s, face="#ffffff", edge=S1, lw=1.1, fs=8.2,
            radius=0.1)
    ax.text(3.2, 8.62, "Domaine Ventes", fontsize=7.8, color=INK3, ha="center")
    ax.text(8.3, 8.62, "Domaine Stocks", fontsize=7.8, color=INK3, ha="center")

    box(ax, 1.4, 0.9, 8.7, 1.15, "Agent Guardrail — contrôle de conformité",
        face=S1, edge=S1, lw=1.3, fs=8.8, weight="bold", tc="#ffffff", radius=0.1)

    box(ax, 14.3, 7.6, 8.0, 2.3,
        "Conseiller de vente\n\nUtilise le conseil face au client",
        face="#ffffff", edge=S2, lw=1.5, fs=9, radius=0.1)
    box(ax, 14.3, 3.9, 8.0, 2.3,
        "Responsable de magasin\n\nArbitre, valide ou rejette",
        face="#ffffff", edge=S2, lw=1.5, fs=9, radius=0.1)
    box(ax, 14.3, 0.9, 8.0, 1.9,
        "Décision engageante\ntoujours humaine",
        face="#fdf3ee", edge=S2, lw=1.3, fs=8.6, tc=INK2, radius=0.1)

    ax.plot([12.0, 12.0], [0.5, 11.9], color=CRIT, lw=2.0,
            linestyle=(0, (6, 4)), zorder=5)
    ax.text(12.0, 12.25, "Frontière de l'automatisation", ha="center",
            fontsize=9, weight="bold", color=CRIT)
    arrow(ax, (11.0, 8.7), (14.2, 8.7), color=INK2, lw=1.5)
    ax.text(12.6, 9.05, "propose", fontsize=8, color=INK2, ha="center")
    arrow(ax, (14.2, 5.0), (11.0, 5.0), color=INK2, lw=1.5)
    ax.text(12.6, 5.35, "arbitre", fontsize=8, color=INK2, ha="center")
    save(fig, "fig_cartographie_acteurs.png")


# ═════════════════════════════════════════════════════════════════════════════
# 6. Chaîne de traduction : objectif métier -> objectif analytique -> critère
# ═════════════════════════════════════════════════════════════════════════════
def fig_chaine_objectifs():
    fig, ax = plt.subplots(figsize=(12.4, 5.8))
    blank(ax, (0, 24), (0, 11.4))

    cols = [
        ("OBJECTIF MÉTIER", S1, 0.5,
         ["OM-1 · Réduire l'écart\nà l'objectif journalier",
          "OM-3 · Réduire les\nruptures de stock",
          "OM-7 · Garantir la\nconformité des conseils"]),
        ("OBJECTIF ANALYTIQUE", S3, 8.3,
         ["OD-1 · Prévoir le CA\nde fin de journée",
          "OD-3 · Prévoir la demande\npar référence",
          "OD-8 · Évaluer la conformité\net la qualité"]),
        ("CRITÈRE DE SUCCÈS", S7, 16.1,
         ["Erreur inférieure à celle\nd'une référence naïve",
          "Gain mesurable face à la\nprévision sans contexte",
          "Aucune diffusion contraire\nà une règle bloquante"]),
    ]
    w = 7.0
    for titre, col, x, items in cols:
        ax.add_patch(Rectangle((x, 9.3), w, 1.15, facecolor=col,
                               edgecolor="none", zorder=3))
        ax.text(x + w / 2, 9.87, titre, ha="center", va="center", fontsize=9.2,
                weight="bold", color="#ffffff", zorder=4)
        for j, it in enumerate(items):
            y = 6.9 - j * 2.55
            box(ax, x, y, w, 1.95, it, face="#ffffff", edge=col, lw=1.3,
                fs=8.6, radius=0.1)

    for j in range(3):
        y = 6.9 + 0.97 - j * 2.55
        arrow(ax, (7.15, y), (8.15, y), color=INK3, lw=1.5)
        arrow(ax, (14.95, y), (15.95, y), color=INK3, lw=1.5)

    ax.text(7.65, 8.55, "se traduit\npar", fontsize=7.6, color=INK3,
            ha="center", va="center", style="italic")
    ax.text(15.45, 8.55, "se mesure\npar", fontsize=7.6, color=INK3,
            ha="center", va="center", style="italic")
    ax.text(12.0, 0.35, "Le critère est fixé avant toute modélisation : il n'est jamais ajusté au résultat obtenu.",
            ha="center", fontsize=8.8, color=INK2, style="italic")
    save(fig, "fig_chaine_objectifs.png")


# ═════════════════════════════════════════════════════════════════════════════
# 7. Matrice des risques (probabilité x impact)
# ═════════════════════════════════════════════════════════════════════════════
def fig_matrice_risques():
    fig, ax = plt.subplots(figsize=(9.0, 6.4))
    ax.set_xlim(0, 3)
    ax.set_ylim(0, 3)

    zones = {(0, 0): "#f2faf2", (1, 0): "#f2faf2", (0, 1): "#f2faf2",
             (2, 0): "#fef8e8", (1, 1): "#fef8e8", (0, 2): "#fef8e8",
             (2, 1): "#fdf1f1", (1, 2): "#fdf1f1", (2, 2): "#fdf1f1"}
    for (cx, cy), color in zones.items():
        ax.add_patch(Rectangle((cx, cy), 1, 1, facecolor=color,
                               edgecolor="#ffffff", linewidth=2.5, zorder=1))

    risques = [
        (2.5, 2.5, "R1", "Recommandation\nerronée du LLM", CRIT),
        (1.5, 2.5, "R2", "Indisponibilité\nfournisseur LLM", CRIT),
        (2.5, 1.5, "R3", "Qualité des\ndonnées", CRIT),
        (1.5, 1.5, "R4", "Décision\nautomatique\nengageante", SERIOUS),
        (0.5, 1.5, "R5", "Base vectorielle\nindisponible", WARN),
        (1.5, 0.5, "R6", "Latence des\ncycles", WARN),
        (2.5, 0.5, "R7", "Charge de fin\nde projet", WARN),
        (0.5, 0.5, "R8", "Dérive du\npérimètre", GOOD),
    ]
    for x, y, code, label, col in risques:
        ax.add_patch(Circle((x, y + 0.16), 0.13, facecolor=col,
                            edgecolor="#ffffff", linewidth=1.6, zorder=4))
        ax.text(x, y + 0.16, code, ha="center", va="center", fontsize=7.4,
                weight="bold", color="#ffffff", zorder=5)
        ax.text(x, y - 0.14, label, ha="center", va="center", fontsize=7.6,
                color=INK, zorder=5, linespacing=1.4)

    ax.set_xticks([0.5, 1.5, 2.5])
    ax.set_xticklabels(["Faible", "Moyen", "Élevé"], fontsize=9)
    ax.set_yticks([0.5, 1.5, 2.5])
    ax.set_yticklabels(["Faible", "Moyenne", "Élevée"], fontsize=9)
    ax.set_xlabel("Impact sur le projet", fontsize=9.5, labelpad=8)
    ax.set_ylabel("Probabilité d'occurrence", fontsize=9.5, labelpad=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    save(fig, "fig_matrice_risques.png")


# ═════════════════════════════════════════════════════════════════════════════
# 8. Diagramme de cas d'utilisation global
# ═════════════════════════════════════════════════════════════════════════════
def _stickman(ax, x, y, label, color=INK):
    ax.add_patch(Circle((x, y + 0.62), 0.155, facecolor="none",
                        edgecolor=color, linewidth=1.5, zorder=4))
    ax.plot([x, x], [y + 0.465, y - 0.02], color=color, lw=1.5, zorder=4)
    ax.plot([x - 0.30, x + 0.30], [y + 0.34, y + 0.34], color=color, lw=1.5, zorder=4)
    ax.plot([x, x - 0.26], [y - 0.02, y - 0.48], color=color, lw=1.5, zorder=4)
    ax.plot([x, x + 0.26], [y - 0.02, y - 0.48], color=color, lw=1.5, zorder=4)
    ax.text(x, y - 0.78, label, ha="center", va="top", fontsize=8.6,
            weight="bold", color=color, linespacing=1.35)


def fig_cas_utilisation():
    fig, ax = plt.subplots(figsize=(12.6, 8.0))
    blank(ax, (0, 24), (0, 15.4))

    ax.add_patch(Rectangle((5.1, 0.6), 13.8, 14.0, facecolor="#ffffff",
                           edgecolor=INK3, linewidth=1.3, zorder=1))
    ax.text(12.0, 14.15, "Moteur agentique de coaching et d'optimisation des stocks",
            ha="center", fontsize=9.4, weight="bold", color=INK)

    def uc(cx, cy, text, color=S1, w=5.2, h=1.16, fs=8.3):
        ax.add_patch(Ellipse((cx, cy), w, h, facecolor="#ffffff",
                             edgecolor=color, linewidth=1.3, zorder=3))
        ax.text(cx, cy, text, ha="center", va="center", fontsize=fs,
                color=INK, zorder=4, linespacing=1.3)

    communs = [
        (12.0, 12.85, "S'authentifier"),
        (12.0, 11.35, "Consulter la performance\ndu point de vente"),
        (12.0, 9.85, "Consulter les alertes\ntemps réel"),
        (12.0, 8.35, "Dialoguer avec l'assistant\nde coaching"),
    ]
    for cx, cy, t in communs:
        uc(cx, cy, t, color=S7)

    conseiller = [
        (8.6, 6.55, "Consulter ses recommandations\nproduits et argumentaires", 6.4),
        (8.6, 5.05, "Demander un\nréapprovisionnement", 6.4),
        (8.6, 3.55, "Suivre ses demandes", 6.4),
    ]
    for cx, cy, t, w in conseiller:
        uc(cx, cy, t, color=S1, w=w, fs=8.0)

    manager = [
        (15.6, 6.55, "Consulter l'état des stocks\net le plan de commande", 6.4),
        (15.6, 5.05, "Arbitrer les demandes\ndes conseillers", 6.4),
        (15.6, 3.55, "Valider une suggestion\nde commande", 6.4),
        (15.6, 2.05, "Superviser la qualité\ndes recommandations", 6.4),
    ]
    for cx, cy, t, w in manager:
        uc(cx, cy, t, color=S2, w=w, fs=8.0)

    _stickman(ax, 2.4, 11.2, "Utilisateur\nauthentifié", color=S7)
    _stickman(ax, 2.4, 5.4, "Conseiller\nde vente", color=S1)
    _stickman(ax, 21.4, 5.4, "Responsable\nde magasin", color=S2)
    _stickman(ax, 21.4, 11.2, "Système\n(agents)", color=INK3)

    for cy in (12.85, 11.35, 9.85, 8.35):
        ax.plot([2.9, 9.42], [11.2, cy], color=S7, lw=0.9, zorder=2)
    for _, cy, _, _ in conseiller:
        ax.plot([2.9, 5.45], [5.4, cy], color=S1, lw=0.9, zorder=2)
    for _, cy, _, _ in manager:
        ax.plot([20.9, 18.75], [5.4, cy], color=S2, lw=0.9, zorder=2)
    for cy in (8.35, 9.85):
        ax.plot([20.9, 14.6], [11.2, cy], color=INK3, lw=0.9,
                linestyle=(0, (3, 2)), zorder=2)

    arrow(ax, (2.4, 6.35), (2.4, 9.95), color=INK2, lw=1.2, style="-|>")
    ax.text(1.95, 8.2, "généralisation", fontsize=7.4, color=INK2, rotation=90,
            va="center", ha="right", style="italic")
    ax.text(21.4, 9.0, "acteur\nsecondaire", fontsize=7.4, color=INK3,
            ha="center", va="center", style="italic")

    ax.text(0.2, 0.05, "Cas communs (violet) · cas du conseiller (bleu) · cas du manager (orange)",
            fontsize=8.2, color=INK2, style="italic")
    save(fig, "fig_cas_utilisation.png")


# ═════════════════════════════════════════════════════════════════════════════
# 9-10. Diagrammes de séquence
# ═════════════════════════════════════════════════════════════════════════════
def _sequence(name, acteurs, messages, notes=(), figsize=(12.6, 7.6),
              top=13.6, step=0.92):
    fig, ax = plt.subplots(figsize=figsize)
    n = len(acteurs)
    width = 24
    blank(ax, (0, width), (0, top + 1.4))
    left, right = 6.0, width - 1.9
    xs = [left + (right - left) * i / (n - 1) for i in range(n)]

    bottom = top - step * (len(messages) + 1) - 0.5
    for x, (label, col) in zip(xs, acteurs):
        box(ax, x - 1.62, top, 3.24, 1.05, label, face="#ffffff", edge=col,
            lw=1.5, fs=8.3, weight="bold", radius=0.08)
        ax.plot([x, x], [bottom, top], color=INK3, lw=0.9,
                linestyle=(0, (3, 3)), zorder=1)
        ax.add_patch(Rectangle((x - 0.13, bottom), 0.26, top - bottom,
                               facecolor="#f0efec", edgecolor=INK3,
                               linewidth=0.7, zorder=2))

    for k, (src, dst, text, kind) in enumerate(messages):
        y = top - step * (k + 1)
        x1, x2 = xs[src], xs[dst]
        if src == dst:
            ax.add_patch(FancyArrowPatch((x1 + 0.15, y + 0.16),
                                         (x1 + 0.15, y - 0.16),
                                         arrowstyle="-|>", mutation_scale=11,
                                         color=INK2, lw=1.2,
                                         connectionstyle="arc3,rad=-2.2",
                                         zorder=4))
            ax.text(x1 + 1.05, y + 0.02, text, fontsize=7.4, color=INK,
                    va="center", ha="left", zorder=5)
        else:
            style = "-|>" if kind != "ret" else "-|>"
            ls = "-" if kind == "call" else (0, (4, 2.5))
            col = {"call": INK2, "ret": S3, "alt": S2}.get(kind, INK2)
            sign = 1 if x2 > x1 else -1
            ax.add_patch(FancyArrowPatch((x1 + 0.14 * sign, y),
                                         (x2 - 0.14 * sign, y),
                                         arrowstyle=style, mutation_scale=12,
                                         color=col, lw=1.25, linestyle=ls,
                                         zorder=4))
            ax.text((x1 + x2) / 2, y + 0.17, text, fontsize=7.4, color=INK,
                    ha="center", va="bottom", zorder=5)

    for k, txt, col in notes:
        y = top - step * (k + 1)
        ax.add_patch(FancyBboxPatch((0.15, y - 0.36), 5.2, 0.72,
                                    boxstyle="round,pad=0,rounding_size=0.06",
                                    facecolor="#fdf8ee", edgecolor=col,
                                    linewidth=1.0, zorder=6))
        ax.text(2.75, y, txt, fontsize=7.6, color=INK2, ha="center",
                va="center", zorder=7)
        ax.plot([5.35, xs[0] - 0.16], [y, y], color=col, lw=0.9,
                linestyle=(0, (2, 2)), zorder=6)
    save(fig, name)


def fig_sequence_coach():
    A = [("Conseiller", S2), ("Interface", S1), ("Superviseur", S7),
         ("Agents\nspécialisés", S1), ("Base de\nconnaissances", S3),
         ("Contrôle de\nconformité", CRIT)]
    M = [
        (0, 1, "pose une question", "call"),
        (1, 2, "transmet la demande", "call"),
        (2, 3, "déclenche les analyses en parallèle", "call"),
        (3, 4, "recherche les argumentaires", "call"),
        (4, 3, "extraits documentaires cités", "ret"),
        (3, 2, "diagnostic, contexte, stock", "ret"),
        (2, 2, "consolide l'analyse", "self"),
        (2, 5, "soumet la réponse formulée", "call"),
        (5, 2, "verdict : diffusion autorisée", "ret"),
        (2, 1, "réponse vérifiée", "ret"),
        (1, 0, "affichage progressif", "ret"),
    ]
    N = [(7, "Réécriture, escalade\nou blocage possibles", CRIT)]
    _sequence("fig_sequence_coach.png", A, M, N, figsize=(12.6, 7.4), top=11.6)


def fig_sequence_reappro():
    A = [("Surveillance\ndes stocks", CRIT), ("Analyse\nInventaire", S1),
         ("Contexte\nInventaire", S1), ("Décision\nInventaire", S1),
         ("Bon de\ncommande", S3), ("Manager", S2)]
    M = [
        (0, 1, "alerte de rupture détectée", "call"),
        (1, 1, "couverture, rotation, risque", "self"),
        (1, 2, "transmet le diagnostic", "call"),
        (2, 2, "demande prévue, promotions, événements", "self"),
        (2, 3, "diagnostic enrichi", "call"),
        (3, 3, "quantité, urgence, justification", "self"),
        (3, 4, "crée une suggestion de commande", "call"),
        (4, 5, "notifie le tableau de suivi", "call"),
        (5, 4, "approuve ou rejette", "alt"),
        (4, 5, "diffusion du nouveau statut", "ret"),
    ]
    N = [(8, "Porte de validation\nhumaine obligatoire", S2)]
    _sequence("fig_sequence_reappro.png", A, M, N, figsize=(12.6, 7.4), top=11.6)


# ═════════════════════════════════════════════════════════════════════════════
# 11. Planning des itérations (Gantt par phase CRISP-DM)
# ═════════════════════════════════════════════════════════════════════════════
def fig_planning_iterations():
    phases = [
        ("Compréhension métier", S1),
        ("Compréhension des données", S2),
        ("Préparation des données", S3),
        ("Modélisation", S4),
        ("Évaluation", S5),
        ("Déploiement", S6),
    ]
    # (phase index, itération début, itération fin)
    barres = [
        (0, 1, 1), (1, 1, 2), (2, 2, 2), (2, 4, 4), (2, 7, 7),
        (3, 2, 3), (3, 4, 10), (4, 6, 6), (4, 10, 11), (5, 11, 12),
    ]
    fig, ax = plt.subplots(figsize=(11.6, 4.6))
    for pi, i0, i1 in barres:
        label, col = phases[pi]
        ax.barh(pi, i1 - i0 + 1 - 0.14, left=i0 - 0.43, height=0.56,
                color=col, edgecolor=SURFACE, linewidth=2.0, zorder=3)

    ax.set_yticks(range(len(phases)))
    ax.set_yticklabels([p[0] for p in phases], fontsize=9)
    ax.invert_yaxis()
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels([f"It. {i}" for i in range(1, 13)], fontsize=8.4)
    ax.set_xlim(0.4, 12.7)
    ax.set_xlabel("Itérations de deux semaines (six mois)", fontsize=9.2,
                  labelpad=8)
    ax.grid(axis="x", color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)

    for x, m in ((2.5, "M1"), (4.5, "M2"), (6.5, "M3"), (8.5, "M4"),
                 (10.5, "M5")):
        ax.axvline(x, color=INK3, lw=0.8, linestyle=(0, (3, 3)), zorder=1)
    save(fig, "fig_planning_iterations.png")


# ═════════════════════════════════════════════════════════════════════════════
# 12. Charge planifiée par itération
# ═════════════════════════════════════════════════════════════════════════════
def fig_charge_iterations():
    pts = [31, 33, 32, 33, 34, 39, 37, 30, 36, 46, 50, 54]
    labels = [f"It. {i}" for i in range(1, 13)]
    moyenne = sum(pts) / len(pts)

    fig, ax = plt.subplots(figsize=(11.2, 4.4))
    bars = ax.bar(labels, pts, color=S1, width=0.62, zorder=3)
    for b in bars[-3:]:
        b.set_color(S2)
    for b, v in zip(bars, pts):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.2, str(v), ha="center",
                va="bottom", fontsize=8.4, color=INK)

    ax.axhline(moyenne, color=INK2, lw=1.4, linestyle=(0, (5, 3)), zorder=4)
    ax.text(11.55, moyenne + 1.4, f"moyenne {moyenne:.0f} pts", ha="right",
            fontsize=8.4, color=INK2)

    ax.set_ylabel("Points de complexité planifiés", fontsize=9.2, labelpad=8)
    ax.set_ylim(0, 62)
    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0, labelsize=8.4)

    ax.bar(0, 0, color=S2, label="Itérations sous vigilance (charge croissante)")
    ax.bar(0, 0, color=S1, label="Itérations à charge nominale")
    handles, lab = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], lab[::-1], frameon=False, fontsize=8.4,
              loc="upper left", ncol=1)
    save(fig, "fig_charge_iterations.png")


# ═════════════════════════════════════════════════════════════════════════════
# 13. Répartition MoSCoW du backlog
# ═════════════════════════════════════════════════════════════════════════════
def fig_repartition_moscow():
    cats = ["Must have", "Should have", "Could have", "Won't have"]
    us = [58, 19, 5, 4]
    fig, ax = plt.subplots(figsize=(9.0, 3.6))
    colors = [S1, S2, S3, INK3]
    bars = ax.barh(cats, us, color=colors, height=0.58, zorder=3)
    for b, v in zip(bars, us):
        ax.text(v + 0.9, b.get_y() + b.get_height() / 2, str(v), va="center",
                fontsize=8.8, color=INK)
    ax.invert_yaxis()
    ax.set_xlabel("Nombre d'items du backlog", fontsize=9.2, labelpad=8)
    ax.set_xlim(0, 65)
    ax.grid(axis="x", color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0, labelsize=9)
    ax.text(0, -0.92, "Les items « Won't have » sont explicitement écartés du périmètre des douze itérations.",
            fontsize=8.2, color=INK2, style="italic")
    save(fig, "fig_repartition_moscow.png")


if __name__ == "__main__":
    print("Génération des figures du chapitre 2 :")
    fig_crispdm_phase1()
    fig_taches_phase1()
    fig_processus_asis()
    fig_couplage_domaines()
    fig_cartographie_acteurs()
    fig_chaine_objectifs()
    fig_matrice_risques()
    fig_cas_utilisation()
    fig_sequence_coach()
    fig_sequence_reappro()
    fig_planning_iterations()
    fig_charge_iterations()
    fig_repartition_moscow()
    print(f"\n{len(os.listdir(OUT))} fichier(s) dans {OUT}")
