"""
figures_schemas.py — Schémas conceptuels des chapitres 3 à 6.

Complète figures.py (chapitres 1-2) et figures_data.py (figures issues de la base).
Même palette, mêmes conventions : 200 dpi, fond clair, aucun titre incrusté.

Usage : python docs/rapport/figures_schemas.py
"""
from __future__ import annotations

import os
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle, Circle, Polygon

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img")
os.makedirs(OUT, exist_ok=True)

S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
S5, S6, S7, S8 = "#e87ba4", "#008300", "#4a3aa7", "#e34948"
GOOD, WARN, SERIOUS, CRIT = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
SURFACE = "#fcfcfb"
INK, INK2, INK3 = "#0b0b0b", "#52514e", "#8a8880"
GRID = "#e3e2dd"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "text.color": INK,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})


def save(fig, name):
    fig.savefig(os.path.join(OUT, name), dpi=200, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    print("  ->", name)


def box(ax, x, y, w, h, text, face="#ffffff", edge=S1, lw=1.4, fs=9,
        weight="normal", tc=INK, radius=0.06, ls="-"):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle=f"round,pad=0,rounding_size={radius}",
                                facecolor=face, edgecolor=edge, linewidth=lw,
                                linestyle=ls, zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color=tc, weight=weight, zorder=3, linespacing=1.4)


def arrow(ax, p1, p2, color=INK2, lw=1.3, style="-|>", ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=12,
                                 color=color, linewidth=lw, linestyle=ls,
                                 connectionstyle=f"arc3,rad={rad}",
                                 shrinkA=2, shrinkB=2, zorder=4))


def label(ax, x, y, t, fs=7.8, color=INK2, ha="center", va="center", weight="normal"):
    ax.text(x, y, t, ha=ha, va=va, fontsize=fs, color=color, weight=weight,
            zorder=5, linespacing=1.35)


def blank(ax, xlim, ylim):
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.axis("off")


def band(ax, x, y, w, h, title, face="#f4f7fc", edge=GRID):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.08",
                                facecolor=face, edgecolor=edge, linewidth=1.1, zorder=1))
    label(ax, x + 0.14, y + h - 0.2, title, fs=8, color=INK3, ha="left", weight="bold")


# ═════════════════════════════════════════════════════════════════════════════
# CHAPITRE 3
# ═════════════════════════════════════════════════════════════════════════════
def fig_schema_relationnel():
    fig, ax = plt.subplots(figsize=(12.4, 7.2))
    blank(ax, (0, 15.5), (0, 9))

    ent = {
        # nom : (x, y, w, h, couleur, attributs)
        "BOUTIQUE":      (0.4, 6.6, 3.0, 1.9, S1, "store_id (PK)\nnom · région · wilaya\nlatitude · longitude"),
        "PRODUIT":       (6.1, 6.6, 3.3, 2.2, S1, "sku (PK)\nnom · catégorie · gamme\nprix · marge\ndélai · MOQ · coûts"),
        "CONSEILLER":    (12.0, 6.6, 3.0, 1.9, S1, "agent_id (PK)\nstore_id (FK)\nquota · spécialité"),
        "TRANSACTION":   (0.4, 3.6, 3.0, 2.0, S3, "id (PK)\nstore_id · agent_id · sku\ndate · heure\nquantité · montant"),
        "OBJECTIF":      (0.4, 0.9, 3.0, 1.7, S3, "store_id · date\nCA cible\ntransactions cible"),
        "VENTE AGRÉGÉE": (4.1, 3.6, 3.0, 2.0, S3, "sku · store_id · jour\nquantité vendue\nvar. calendaires\npromo · événement"),
        "NIVEAU STOCK":  (7.8, 3.6, 3.0, 2.0, S4, "sku · store_id\nquantité · réservée\njours de couverture"),
        "PRÉV. DEMANDE": (11.5, 3.6, 3.3, 2.0, S4, "sku · store_id · date\ndemande de base\ndemande corrigée"),
        "ALERTE":        (4.1, 0.9, 3.0, 1.7, S2, "sku · store_id\ntype · sévérité\nstatut"),
        "RECOMMANDATION":(7.8, 0.9, 3.0, 1.7, S2, "id (PK)\nsku · store_id\naction · quantité\nstatut"),
        "BON COMMANDE":  (11.5, 0.9, 3.3, 1.7, S2, "po_id (PK)\nrecommendation_id (FK)\nfournisseur · statut"),
    }

    for name, (x, y, w, h, col, attrs) in ent.items():
        box(ax, x, y, w, h, "", face="#ffffff", edge=col, lw=1.5)
        ax.add_patch(FancyBboxPatch((x, y + h - 0.46), w, 0.46,
                                    boxstyle="round,pad=0,rounding_size=0.06",
                                    facecolor=col, edgecolor=col, zorder=3))
        label(ax, x + w / 2, y + h - 0.23, name, fs=8.4, color="#ffffff", weight="bold")
        label(ax, x + w / 2, y + (h - 0.46) / 2, attrs, fs=7.2, color=INK2)

    def link(a, b, rad=0.0, color=INK3, ls="-"):
        xa, ya, wa, ha_, *_ = ent[a]
        xb, yb, wb, hb, *_ = ent[b]
        arrow(ax, (xa + wa / 2, ya), (xb + wb / 2, yb + hb), color=color,
              lw=1.1, style="-", ls=ls, rad=rad)

    # rattachements aux référentiels
    for src in ("TRANSACTION", "VENTE AGRÉGÉE", "NIVEAU STOCK", "PRÉV. DEMANDE"):
        xa, ya, wa, ha_, *_ = ent[src]
        arrow(ax, (xa + wa / 2, ya + ha_), (7.75, 6.6), color=GRID, lw=1.0, style="-", rad=0.06)

    link("TRANSACTION", "OBJECTIF", color=GRID)
    link("VENTE AGRÉGÉE", "ALERTE", color=GRID)
    link("NIVEAU STOCK", "RECOMMANDATION", color=GRID)
    link("PRÉV. DEMANDE", "BON COMMANDE", color=GRID)

    # chaîne causale, mise en évidence
    arrow(ax, (7.1, 1.75), (7.8, 1.75), color=S2, lw=2.0)
    arrow(ax, (10.8, 1.75), (11.5, 1.75), color=S2, lw=2.0)
    label(ax, 7.45, 2.05, "déclenche", fs=7.2, color=S2)
    label(ax, 11.15, 2.05, "engendre", fs=7.2, color=S2)

    band(ax, 0.15, 6.35, 15.2, 0.0, "")
    label(ax, 0.3, 8.75, "RÉFÉRENTIELS", fs=8.6, color=S1, ha="left", weight="bold")
    label(ax, 0.3, 5.85, "OBSERVATIONS — ventes", fs=8.6, color=S3, ha="left", weight="bold")
    label(ax, 7.9, 5.85, "OBSERVATIONS — stocks", fs=8.6, color=S4, ha="left", weight="bold")
    label(ax, 0.3, 2.85, "DÉCISIONS  — chaîne causale du système", fs=8.6, color=S2, ha="left", weight="bold")

    save(fig, "fig_schema_relationnel.png")


def fig_pipeline_preparation():
    fig, ax = plt.subplots(figsize=(12.6, 5.0))
    blank(ax, (0, 16), (0, 6.4))

    sources = [
        ("Historique\ncommercial", S3), ("Flux temps\nréel", S3),
        ("Stocks et\napprovisionnement", S4), ("Signaux de\ncontexte", S5),
    ]
    for i, (t, c) in enumerate(sources):
        box(ax, 0.2, 4.85 - i * 1.22, 2.35, 0.95, t, face="#ffffff", edge=c, fs=7.8)

    etapes = [
        ("3.5.1\nSélection", "critères\nd'inclusion", S1),
        ("3.5.2\nNettoyage", "manquants\naberrants", S1),
        ("3.5.3\nConstruction", "variables\ncalendaires,\nde stock,\nde contexte", S2),
        ("3.5.4\nIntégration", "jointures\ninter-domaines", S1),
        ("3.5.5\nFormatage", "structures\nd'entrée", S1),
    ]
    x = 3.35
    for i, (t, sub, c) in enumerate(etapes):
        box(ax, x, 2.5, 1.95, 2.3, f"{t}\n\n{sub}", face="#ffffff", edge=c,
            lw=1.9 if c == S2 else 1.3, fs=7.6)
        if i:
            arrow(ax, (x - 0.42, 3.65), (x - 0.03, 3.65), color=INK3, lw=1.3)
        x += 2.37

    for i in range(4):
        arrow(ax, (2.6, 5.32 - i * 1.22), (3.3, 3.9), color=GRID, lw=1.1, rad=0.08)

    sorties = [("Ventes\njournalières\n(OD-1, OD-2)", S3),
               ("Demande par\nréférence\n(OD-3 à OD-5)", S4),
               ("Connaissance\nmétier\n(OD-7)", S5)]
    for i, (t, c) in enumerate(sorties):
        box(ax, 13.5, 4.5 - i * 1.55, 2.3, 1.3, t, face="#ffffff", edge=c, fs=7.6)
        arrow(ax, (13.15, 3.65), (13.45, 5.15 - i * 1.55), color=GRID, lw=1.1, rad=0.06)

    band(ax, 3.25, 1.05, 11.5, 1.05, "")
    label(ax, 9.0, 1.58, "Versionnement du schéma par migrations — aucune modification de structure à l'exécution",
          fs=8.4, color=INK2, weight="bold")

    label(ax, 1.38, 5.98, "SOURCES", fs=8.4, color=INK3, weight="bold")
    label(ax, 8.9, 5.98, "PRÉPARATION (phase 3 de CRISP-DM)", fs=8.4, color=INK3, weight="bold")
    label(ax, 14.65, 5.98, "JEU FINAL", fs=8.4, color=INK3, weight="bold")

    save(fig, "fig_pipeline_preparation.png")


def fig_decoupage_temporel():
    fig, ax = plt.subplots(figsize=(11.2, 4.6))
    blank(ax, (0, 13), (0, 6.2))

    x0, x1 = 1.3, 12.4
    for i in range(6):
        y = 5.05 - i * 0.72
        fin_train = x0 + 4.4 + i * 1.13
        ax.add_patch(Rectangle((x0, y - 0.24), fin_train - x0, 0.48,
                               facecolor=S1, alpha=0.30, edgecolor=S1, lw=1.0, zorder=2))
        ax.add_patch(Rectangle((fin_train, y - 0.24), 1.13, 0.48,
                               facecolor=S2, alpha=0.85, edgecolor=S2, lw=1.0, zorder=3))
        label(ax, 0.62, y, f"Pli {i + 1}", fs=8, color=INK2)
        label(ax, fin_train + 0.565, y, "28 j", fs=7, color="#ffffff", weight="bold")

    ax.plot([x0, x1], [0.62, 0.62], color=INK3, lw=1.2, zorder=3)
    for xx, t in ((x0, "oct. 2024"), (x0 + 4.4, "févr. 2026"), (x1, "juil. 2026")):
        ax.plot([xx, xx], [0.5, 0.74], color=INK3, lw=1.2, zorder=3)
        label(ax, xx, 0.28, t, fs=7.6, color=INK2)
    label(ax, 6.8, 0.02, "Temps", fs=8, color=INK3)

    from matplotlib.patches import Patch
    ax.legend(handles=[
        Patch(facecolor=S1, alpha=0.30, edgecolor=S1, label="Apprentissage (historique expansif)"),
        Patch(facecolor=S2, alpha=0.85, edgecolor=S2, label="Test (28 jours, jamais vus)"),
    ], frameon=False, fontsize=8.4, loc="upper right", bbox_to_anchor=(1.0, 1.02))

    label(ax, 6.4, 5.85,
          "Origine glissante : l'apprentissage ne contient jamais d'observation postérieure au test",
          fs=8.6, color=INK, weight="bold")
    save(fig, "fig_decoupage_temporel.png")


# ═════════════════════════════════════════════════════════════════════════════
# CHAPITRE 4
# ═════════════════════════════════════════════════════════════════════════════
def fig_archi_trois_tiers():
    fig, ax = plt.subplots(figsize=(11.4, 5.6))
    blank(ax, (0, 13), (0, 7))

    band(ax, 0.3, 4.55, 3.5, 2.2, "TIER PRÉSENTATION")
    box(ax, 0.6, 5.55, 2.9, 0.75, "Espace conseiller", face="#ffffff", edge=S1, fs=8)
    box(ax, 0.6, 4.72, 2.9, 0.75, "Espace responsable", face="#ffffff", edge=S1, fs=8)

    band(ax, 4.6, 0.4, 4.7, 6.35, "TIER APPLICATIF")
    couches = [("Exposition des services", S1), ("Orchestration", S7),
               ("Agents", S2), ("Services de calcul", S3), ("Dépôts de données", S4)]
    for i, (t, c) in enumerate(couches):
        box(ax, 4.9, 5.5 - i * 1.05, 4.1, 0.82, t, face="#ffffff", edge=c, fs=8.2)
        if i:
            arrow(ax, (6.95, 5.5 - (i - 1) * 1.05), (6.95, 6.32 - i * 1.05),
                  color=GRID, lw=1.1, style="-")

    band(ax, 10.1, 1.4, 2.6, 5.35, "TIER DONNÉES")
    for i, (t, c) in enumerate([("Base\nrelationnelle", S4), ("Cache et\ndiffusion", S4),
                                ("Base\nvectorielle", S5)]):
        box(ax, 10.35, 5.4 - i * 1.35, 2.1, 1.05, t, face="#ffffff", edge=c, fs=7.8)
        arrow(ax, (9.05, 1.9), (10.3, 5.92 - i * 1.35), color=GRID, lw=1.0, rad=0.1)

    for i, (t, y) in enumerate([("appels de service", 6.05), ("diffusion temps réel", 5.5),
                                ("réponse progressive", 4.95)]):
        arrow(ax, (3.85, y), (4.85, y - 0.1), color=INK3, lw=1.2, rad=0.03)
        label(ax, 4.35, y + 0.24, t, fs=6.6, color=INK3)

    label(ax, 6.95, 0.12,
          "Principe : le calcul qui engage une décision reste déterministe (couche « services »)",
          fs=8.2, color=S3, weight="bold")
    save(fig, "fig_archi_trois_tiers.png")


def fig_archi_couches():
    fig, ax = plt.subplots(figsize=(11.6, 6.0))
    blank(ax, (0, 14), (0, 7.4))

    couches = [
        ("EXPOSITION", S1, ["Services REST", "Flux temps réel", "Réponse progressive", "Contrôle d'accès"]),
        ("ORCHESTRATION", S7, ["Superviseur", "Déclencheurs", "État partagé", "Réducteurs de fusion"]),
        ("AGENTS", S2, ["Analyste", "Stratège", "Coach", "Garde-fou",
                        "Analyse", "Contexte", "Décision"]),
        ("SERVICES", S3, ["Moteur séries temp.", "Calculs de stock", "Score produit",
                          "Recherche documentaire"]),
        ("DÉPÔTS", S4, ["Ventes", "Stocks", "Approvisionnement", "Contexte marché", "Transverse"]),
    ]
    y = 6.15
    for nom, col, items in couches:
        h = 1.12
        ax.add_patch(FancyBboxPatch((0.3, y - h), 13.4, h,
                                    boxstyle="round,pad=0,rounding_size=0.07",
                                    facecolor="#ffffff", edgecolor=col, linewidth=1.5, zorder=2))
        ax.add_patch(FancyBboxPatch((0.3, y - h), 2.5, h,
                                    boxstyle="round,pad=0,rounding_size=0.07",
                                    facecolor=col, edgecolor=col, zorder=3))
        label(ax, 1.55, y - h / 2, nom, fs=8.6, color="#ffffff", weight="bold")
        n = len(items)
        w = 10.5 / n
        for i, it in enumerate(items):
            box(ax, 3.05 + i * w, y - h + 0.2, w - 0.16, h - 0.4, it,
                face="#f7f9fc", edge=GRID, lw=1.0, fs=7.2)
        if y < 6.15:
            arrow(ax, (7.0, y + 0.28), (7.0, y - 0.02), color=INK3, lw=1.2)
        y -= 1.42

    ax.add_patch(FancyBboxPatch((2.95, 2.55), 10.85, 2.85,
                                boxstyle="round,pad=0,rounding_size=0.08",
                                facecolor="none", edgecolor=S2, linewidth=2.0,
                                linestyle="--", zorder=5))
    label(ax, 8.4, 0.42,
          "Frontière agents / services : aucun agent ne calcule une valeur engageant une décision",
          fs=8.4, color=S2, weight="bold")
    save(fig, "fig_archi_couches.png")


def fig_composants():
    fig, ax = plt.subplots(figsize=(11.4, 5.6))
    blank(ax, (0, 13), (0, 7))

    band(ax, 0.3, 0.4, 12.4, 6.4, "APPLICATION — module unique, frontières internes strictes")

    band(ax, 0.7, 3.5, 5.6, 2.7, "DOMAINE VENTES")
    for i, t in enumerate(["Agents ventes", "Prévision CA", "Coaching et RAG"]):
        box(ax, 0.95, 5.35 - i * 0.62, 5.1, 0.5, t, face="#ffffff", edge=S3, fs=7.8)

    band(ax, 6.7, 3.5, 5.6, 2.7, "DOMAINE STOCKS")
    for i, t in enumerate(["Agents stocks", "Prévision demande", "Décision et approvisionnement"]):
        box(ax, 6.95, 5.35 - i * 0.62, 5.1, 0.5, t, face="#ffffff", edge=S4, fs=7.8)

    band(ax, 0.7, 0.75, 11.6, 2.45, "NOYAU TRANSVERSE")
    for i, t in enumerate(["Configuration", "Accès base", "Cache", "Journalisation",
                           "Observabilité", "Qualité"]):
        box(ax, 0.95 + i * 1.92, 1.65, 1.78, 0.62, t, face="#ffffff", edge=S7, fs=7.2)
    box(ax, 0.95, 0.95, 11.1, 0.55, "Migrations versionnées — source unique de vérité du schéma",
        face="#f7f9fc", edge=GRID, lw=1.0, fs=7.6)

    arrow(ax, (6.3, 4.85), (6.7, 4.85), color=INK3, lw=1.4, style="<|-|>")
    label(ax, 6.5, 5.15, "score\ncross-domaine", fs=6.8, color=S2)

    save(fig, "fig_composants.png")


def fig_classes_metier():
    fig, ax = plt.subplots(figsize=(12.0, 6.4))
    blank(ax, (0, 14), (0, 8))

    def cls(x, y, w, h, nom, attrs, col):
        box(ax, x, y, w, h, "", face="#ffffff", edge=col, lw=1.4)
        ax.add_patch(Rectangle((x, y + h - 0.42), w, 0.42, facecolor=col,
                               edgecolor=col, zorder=3))
        label(ax, x + w / 2, y + h - 0.21, nom, fs=8, color="#ffffff", weight="bold")
        label(ax, x + 0.12, y + h - 0.62, attrs, fs=6.9, color=INK2, ha="left", va="top")

    cls(0.3, 5.9, 2.7, 1.75, "PointDeVente", "store_id\nnom, région\ncoordonnées", S1)
    cls(3.4, 5.9, 2.7, 1.75, "Produit", "sku\ncatégorie, prix\ndélai, MOQ", S1)
    cls(6.5, 5.9, 2.7, 1.75, "Conseiller", "agent_id\nquota, profil", S1)
    cls(9.6, 5.9, 3.0, 1.75, "Fournisseur", "supplier_id\ncatalogue, délais", S1)

    cls(0.3, 3.35, 2.7, 1.95, "Transaction", "date, heure\nquantité\nmontant, marge", S3)
    cls(3.4, 3.35, 2.7, 1.95, "NiveauStock", "quantité\nréservée\ncouverture", S4)
    cls(6.5, 3.35, 2.7, 1.95, "PrévisionDemande", "date\ndemande base\ndemande corrigée", S4)
    cls(9.6, 3.35, 3.0, 1.95, "Objectif", "date\nCA cible\ntransactions", S3)

    cls(0.3, 0.5, 2.7, 2.0, "Alerte", "type, sévérité\nstatut\ncycle_id", S2)
    cls(3.4, 0.5, 2.7, 2.0, "Recommandation", "action, quantité\njustification\nstatut", S2)
    cls(6.5, 0.5, 2.7, 2.0, "BonDeCommande", "quantité\nstatut, dates\nreco_id", S2)
    cls(9.6, 0.5, 3.0, 2.0, "RetourHumain", "décision\nmotif\nhorodatage", S2)

    for x in (1.65, 4.75, 7.85):
        arrow(ax, (x, 3.3), (x, 2.55), color=INK3, lw=1.2)
    arrow(ax, (3.0, 1.5), (3.4, 1.5), color=S2, lw=1.8)
    arrow(ax, (6.1, 1.5), (6.5, 1.5), color=S2, lw=1.8)
    arrow(ax, (9.2, 1.5), (9.6, 1.5), color=S2, lw=1.8)
    for x in (1.65, 4.75, 7.85, 11.1):
        arrow(ax, (x, 5.85), (x, 5.35), color=GRID, lw=1.1, style="-")

    label(ax, 0.35, 7.85, "RÉFÉRENTIEL", fs=8.4, color=S1, ha="left", weight="bold")
    label(ax, 0.35, 5.52, "OBSERVATION", fs=8.4, color=S3, ha="left", weight="bold")
    label(ax, 0.35, 2.68, "DÉCISION — chaque production du système est réifiée et tracée",
          fs=8.4, color=S2, ha="left", weight="bold")
    save(fig, "fig_classes_metier.png")


def fig_pipeline_prevision_ca():
    fig, ax = plt.subplots(figsize=(12.4, 5.2))
    blank(ax, (0, 15.5), (0, 6.6))

    box(ax, 0.2, 3.9, 2.3, 1.35, "Série de CA\njournalier\n+ variables", face="#ffffff", edge=S3, fs=7.6)
    box(ax, 0.2, 1.5, 2.3, 1.35, "Ventes du jour\nen cours\n(temps réel)", face="#ffffff", edge=S3, fs=7.6)

    band(ax, 2.95, 2.6, 4.3, 3.85, "CASCADE DE PRÉVISION")
    for i, (t, c) in enumerate([("1. Modèle appris global", S2),
                                ("2. Lissage exponentiel\nsaisonnier (m = 7)", S1),
                                ("3. Moyenne mobile", INK3)]):
        box(ax, 3.2, 5.25 - i * 1.02, 3.8, 0.82, t, face="#ffffff", edge=c,
            lw=1.9 if i == 0 else 1.2, fs=7.6)
        if i:
            label(ax, 7.35, 5.66 - i * 1.02, "si\nindispo.", fs=6.3, color=INK3)

    box(ax, 7.75, 4.55, 3.0, 1.3, "Profil intra-journalier\npar jour de semaine", face="#ffffff",
        edge=S1, fs=7.6)
    box(ax, 7.75, 2.75, 3.0, 1.3, "Prévision hybride\nde fin de journée", face="#ffffff",
        edge=S2, lw=1.8, fs=7.6)
    box(ax, 7.75, 0.95, 3.0, 1.3, "Écart horaire\nnormalisé  z = (y−µ)/σ", face="#ffffff",
        edge=S4, fs=7.6)

    for i, (t, c) in enumerate([("Prévision de fin\nde journée (OD-1)", S2),
                                ("Statut horaire :\nconforme / surveillé /\nen alerte (OD-2)", S4),
                                ("Urgence composite\net faisabilité", S8)]):
        box(ax, 11.9, 4.6 - i * 1.75, 3.4, 1.4, t, face="#ffffff", edge=c, fs=7.6)

    arrow(ax, (2.55, 4.55), (3.15, 4.55), color=INK3, lw=1.3)
    arrow(ax, (2.55, 2.15), (7.7, 1.6), color=INK3, lw=1.2, rad=-0.08)
    arrow(ax, (2.55, 2.15), (7.7, 3.4), color=INK3, lw=1.2, rad=-0.12)
    arrow(ax, (7.3, 4.35), (7.7, 5.2), color=INK3, lw=1.2, rad=0.1)
    arrow(ax, (9.25, 4.5), (9.25, 4.1), color=INK3, lw=1.3)
    arrow(ax, (10.8, 3.4), (11.85, 5.3), color=INK3, lw=1.2, rad=0.08)
    arrow(ax, (10.8, 1.6), (11.85, 3.3), color=INK3, lw=1.2, rad=0.08)
    arrow(ax, (10.8, 1.4), (11.85, 1.5), color=INK3, lw=1.2)

    label(ax, 7.7, 0.28, "Aucun modèle de langage n'intervient dans cette chaîne — moins d'une seconde",
          fs=8.2, color=S3, weight="bold")
    save(fig, "fig_pipeline_prevision_ca.png")


def fig_pipeline_prevision_demande():
    fig, ax = plt.subplots(figsize=(12.4, 4.9))
    blank(ax, (0, 15.5), (0, 6.2))

    box(ax, 0.2, 3.4, 2.5, 1.5, "Historique agrégé\npar référence\net point de vente", face="#ffffff",
        edge=S3, fs=7.6)
    box(ax, 0.2, 1.1, 2.5, 1.5, "Signaux rapides :\npromo, événement,\nrupture, météo", face="#ffffff",
        edge=S5, fs=7.6)

    band(ax, 3.15, 2.9, 3.8, 2.9, "ÉTAGE 1 — signal lent")
    box(ax, 3.4, 3.25, 3.3, 1.9, "Décomposition\nsaisonnière multiple\n\nhorizon 30 jours\nrecalcul tous les 2 jours",
        face="#ffffff", edge=S1, lw=1.6, fs=7.4)

    band(ax, 7.4, 2.9, 3.8, 2.9, "ÉTAGE 2 — signal rapide")
    box(ax, 7.65, 3.25, 3.3, 1.9, "Modèle appris\nde correction\n\nprédit l'écart, pas la demande\nhorizon 7 jours",
        face="#ffffff", edge=S2, lw=1.8, fs=7.4)

    box(ax, 11.65, 3.45, 1.6, 1.5, "Borne de\nsécurité\n[0 ; 3× base]", face="#fdf4ef",
        edge=S8, fs=7.2)
    box(ax, 13.55, 3.45, 1.75, 1.5, "Demande\nprévue\n(OD-3)", face="#ffffff", edge=S4,
        lw=1.6, fs=7.8)

    box(ax, 5.9, 0.55, 5.4, 1.35, "Mesure quotidienne de la précision\nprévision de la veille  vs  ventes réelles",
        face="#f2f8f4", edge=S3, lw=1.4, fs=7.8)

    arrow(ax, (2.75, 4.15), (3.35, 4.15), color=INK3, lw=1.3)
    arrow(ax, (6.75, 4.2), (7.6, 4.2), color=INK3, lw=1.3)
    arrow(ax, (2.75, 1.85), (7.6, 3.6), color=S5, lw=1.2, rad=-0.13)
    arrow(ax, (11.0, 4.2), (11.6, 4.2), color=INK3, lw=1.3)
    arrow(ax, (13.25, 4.2), (13.5, 4.2), color=INK3, lw=1.3)
    arrow(ax, (14.4, 3.4), (11.35, 1.9), color=S3, lw=1.2, rad=0.15)
    label(ax, 4.9, 0.15, "Filtre d'entrée : couples disposant d'au moins 90 jours d'historique",
          fs=7.8, color=INK2)
    save(fig, "fig_pipeline_prevision_demande.png")


def fig_arbre_decision_stock():
    fig, ax = plt.subplots(figsize=(11.6, 6.2))
    blank(ax, (0, 13), (0, 7.6))

    box(ax, 4.7, 6.5, 3.6, 0.85, "Référence évaluée\nstock, demande, délai", face="#ffffff",
        edge=S1, lw=1.5, fs=8)

    box(ax, 4.7, 5.1, 3.6, 0.85, "Contraintes bloquantes ?\nfournisseur · budget · fin de vie",
        face="#fdf6ef", edge=S4, fs=7.6)
    arrow(ax, (6.5, 6.45), (6.5, 6.0), color=INK3, lw=1.3)

    box(ax, 9.4, 5.1, 3.2, 0.85, "SURVEILLER\ndécision déléguée", face="#f6f6f4",
        edge=INK3, lw=1.5, fs=7.8, weight="bold")
    arrow(ax, (8.35, 5.52), (9.35, 5.52), color=INK3, lw=1.3)
    label(ax, 8.85, 5.78, "oui", fs=7, color=INK3)

    box(ax, 4.7, 3.7, 3.6, 0.85, "Stock projeté < point de commande ?", face="#ffffff",
        edge=S1, fs=7.6)
    arrow(ax, (6.5, 5.05), (6.5, 4.6), color=INK3, lw=1.3)
    label(ax, 6.75, 4.82, "non", fs=7, color=INK3)

    box(ax, 0.4, 3.7, 3.4, 0.85, "Commande déjà en cours ?", face="#ffffff", edge=S1, fs=7.6)
    arrow(ax, (4.65, 4.12), (3.85, 4.12), color=INK3, lw=1.3)
    label(ax, 4.25, 4.38, "oui", fs=7, color=INK3)

    box(ax, 0.4, 1.9, 3.4, 0.95, "ACCÉLÉRER\nla commande en cours\narrivera trop tard",
        face="#fdf1ec", edge=S2, lw=1.8, fs=7.6, weight="bold")
    arrow(ax, (2.1, 3.65), (2.1, 2.9), color=S2, lw=1.4)
    label(ax, 2.42, 3.3, "oui", fs=7, color=S2)

    box(ax, 4.7, 1.9, 3.6, 0.95, "COMMANDER\nquantité économique,\narrondie aux contraintes",
        face="#fdf1ec", edge=S2, lw=2.0, fs=7.6, weight="bold")
    arrow(ax, (2.1, 3.65), (4.9, 2.9), color=S2, lw=1.4, rad=-0.1)
    label(ax, 3.6, 3.05, "non", fs=7, color=S2)

    box(ax, 9.4, 3.55, 3.2, 1.1, "MAINTENIR\nsituation saine\nou surstock — ne pas commander",
        face="#f1f9f3", edge=S3, lw=1.7, fs=7.6, weight="bold")
    arrow(ax, (8.35, 4.12), (9.35, 4.12), color=S3, lw=1.4)
    label(ax, 8.85, 4.38, "non", fs=7, color=S3)

    band(ax, 0.4, 0.35, 12.2, 1.2, "")
    label(ax, 6.5, 0.95,
          "Toute action se conclut par une proposition — jamais par un engagement automatique.\n"
          "Le point d'arrêt humain est obligatoire avant création d'un bon de commande.",
          fs=8.2, color=INK2, weight="bold")
    save(fig, "fig_arbre_decision_stock.png")


def fig_graphe_orchestration():
    fig, ax = plt.subplots(figsize=(12.9, 6.8))
    blank(ax, (0, 15.9), (0, 8.2))

    box(ax, 0.2, 3.9, 1.6, 0.8, "Initialisation\ndu cycle", face="#ffffff", edge=S1, fs=7.4)

    branches = [("Branche VENTES\nAnalyste → Stratège", S3),
                ("Branche CONNAISSANCE\nrecherche documentaire", S5),
                ("Branche CONTEXTE\nmétéo, fériés, événements", S5),
                ("Branche STOCKS\nAnalyse ∥ Contexte → Décision", S4)]
    band(ax, 2.25, 1.15, 3.9, 6.4, "FAN-OUT — exécution parallèle")
    for i, (t, c) in enumerate(branches):
        box(ax, 2.5, 6.15 - i * 1.5, 3.4, 1.05, t, face="#ffffff", edge=c, fs=7.2)
        arrow(ax, (1.85, 4.3), (2.45, 6.68 - i * 1.5), color=GRID, lw=1.1, rad=0.05)
        arrow(ax, (5.95, 6.68 - i * 1.5), (6.5, 4.3), color=GRID, lw=1.1, rad=0.05)

    box(ax, 6.55, 3.75, 1.7, 1.1, "Fusion\ndes deltas\n(réducteurs)", face="#f4f7fc",
        edge=S7, lw=1.6, fs=7.2)

    box(ax, 8.65, 5.5, 2.2, 0.95, "Fusion\ncross-domaine\nscore 6 critères", face="#ffffff",
        edge=S2, fs=7.4)
    box(ax, 8.65, 3.95, 2.2, 0.95, "Contrôle de\nconformité\n7 règles", face="#fdf6ef",
        edge=S4, lw=1.7, fs=7.4)
    arrow(ax, (8.3, 4.4), (8.6, 5.9), color=INK3, lw=1.3, rad=0.1)
    arrow(ax, (9.75, 5.45), (9.75, 4.95), color=INK3, lw=1.3)

    issues = [("APPROBATION\n→ diffusion", S3, 6.7),
              ("RÉÉCRITURE\n→ retour coach (1 fois)", S4, 5.2),
              ("ESCALADE\n→ validation humaine", S2, 3.7),
              ("BLOCAGE\n→ message neutre", S8, 2.2)]
    for t, c, y in issues:
        box(ax, 11.3, y - 0.45, 2.9, 0.95, t, face="#ffffff", edge=c, lw=1.5, fs=7.2)
        arrow(ax, (10.9, 4.42), (11.25, y), color=c, lw=1.2, rad=0.06)

    # boucle de réécriture : repart du bord gauche de l'état vers la fusion, par le haut
    arrow(ax, (11.25, 5.4), (9.75, 6.5), color=S4, lw=1.3, ls="--", rad=-0.32)
    label(ax, 10.1, 7.0, "boucle unique\nde réécriture", fs=6.8, color=S4)

    box(ax, 11.3, 0.5, 2.9, 0.85, "Mémorisation du cycle\n→ corpus documentaire", face="#f2f8f4",
        edge=S3, fs=7.2)
    # collecteur vertical à droite des états, sans traverser aucune boîte
    ax.plot([14.62, 14.62], [1.35, 6.7], color=GRID, lw=1.0, zorder=1)
    for _, _, y in issues:
        ax.plot([14.2, 14.62], [y, y], color=GRID, lw=1.0, zorder=1)
    arrow(ax, (14.62, 1.6), (14.2, 1.15), color=GRID, lw=1.0, rad=0.2)

    label(ax, 7.6, 0.12,
          "Aucun agent n'appelle un autre agent : toute communication transite par l'état partagé",
          fs=8.2, color=S7, weight="bold")
    save(fig, "fig_graphe_orchestration.png")


def fig_automate_guardrail():
    fig, ax = plt.subplots(figsize=(11.4, 5.0))
    blank(ax, (0, 13), (0, 6.2))

    box(ax, 0.3, 2.6, 2.3, 1.1, "Recommandation\nproduite", face="#ffffff", edge=S1, fs=7.8)
    box(ax, 3.1, 2.35, 2.5, 1.6, "Évaluation\ndes 7 règles\n\nsévérité maximale", face="#fdf6ef",
        edge=S4, lw=1.7, fs=7.6)
    arrow(ax, (2.65, 3.15), (3.05, 3.15), color=INK3, lw=1.3)

    etats = [("APPROBATION", "aucune règle violée", S3, 5.15),
             ("RÉÉCRITURE", "G2 · G3 · G5", S4, 3.65),
             ("ESCALADE", "G6 · G7", S2, 2.15),
             ("BLOCAGE", "G1 · G4", S8, 0.65)]
    for nom, regles, col, y in etats:
        box(ax, 6.4, y - 0.05, 2.9, 1.05, f"{nom}\n{regles}", face="#ffffff", edge=col,
            lw=1.7, fs=7.4, weight="bold")
        arrow(ax, (5.65, 3.15), (6.35, y + 0.48), color=col, lw=1.3, rad=0.08)

    sorties = [("Diffusée au destinataire", S3, 5.15),
               ("Nouvelle tentative\n— une seule boucle", S4, 3.65),
               ("File de validation humaine", S2, 2.15),
               ("Message neutre —\nl'originale n'est jamais diffusée", S8, 0.65)]
    for t, col, y in sorties:
        box(ax, 9.6, y + 0.05, 3.2, 0.9, t, face="#f7f9fc", edge=GRID, lw=1.0, fs=7.0)
        arrow(ax, (9.35, y + 0.48), (9.55, y + 0.5), color=col, lw=1.2)

    # retour de réécriture : contourne par la gauche, au-dessus, sans traverser d'état
    arrow(ax, (6.35, 4.6), (4.35, 3.95), color=S4, lw=1.3, ls="--", rad=-0.45)
    label(ax, 5.05, 5.25, "retour au rédacteur\navec le motif précis", fs=6.9, color=S4)

    label(ax, 6.4, 0.05, "Le verdict est déterministe et rejouable : il peut toujours être expliqué par la règle qui l'a produit",
          fs=8.0, color=INK2, weight="bold")
    save(fig, "fig_automate_guardrail.png")


def fig_cycle_bon_commande():
    fig, ax = plt.subplots(figsize=(12.2, 3.6))
    blank(ax, (0, 15), (0, 4.4))

    etapes = [("SUGGÉRÉ", "produit par\nl'agent Décision", S1),
              ("APPROUVÉ", "décision\nhumaine", S2),
              ("SOUMIS", "transmis au\nfournisseur", S7),
              ("CONFIRMÉ", "accusé\nfournisseur", S7),
              ("EXPÉDIÉ", "en transit", S4),
              ("REÇU", "stock mis à jour\nmouvement écrit", S3)]
    x = 0.3
    for i, (nom, sub, col) in enumerate(etapes):
        box(ax, x, 1.55, 2.15, 1.35, f"{nom}\n\n{sub}", face="#ffffff", edge=col,
            lw=1.8 if i in (1, 5) else 1.2, fs=7.4, weight="bold" if i in (1, 5) else "normal")
        if i:
            arrow(ax, (x - 0.28, 2.22), (x - 0.03, 2.22), color=INK3, lw=1.3)
        x += 2.43

    ax.add_patch(FancyBboxPatch((2.6, 1.35), 2.55, 1.75,
                                boxstyle="round,pad=0,rounding_size=0.08",
                                facecolor="none", edgecolor=S2, lw=2.0, ls="--", zorder=5))
    label(ax, 3.87, 3.35, "POINT D'ARRÊT HUMAIN", fs=8.2, color=S2, weight="bold")

    box(ax, 2.6, 0.25, 2.55, 0.9, "REJETÉ — motif capturé\net réinjecté dans le système",
        face="#fdf1ec", edge=S8, fs=7.2)
    arrow(ax, (3.87, 1.5), (3.87, 1.2), color=S8, lw=1.3)

    box(ax, 5.6, 0.25, 8.7, 0.9,
        "La réception ferme la chaîne causale : l'effet réel d'une décision du système devient mesurable",
        face="#f2f8f4", edge=S3, lw=1.3, fs=7.8)
    arrow(ax, (13.5, 1.5), (13.5, 1.2), color=S3, lw=1.3)
    save(fig, "fig_cycle_bon_commande.png")


def fig_cascade_llm():
    fig, ax = plt.subplots(figsize=(11.4, 4.6))
    blank(ax, (0, 13), (0, 5.8))

    box(ax, 0.3, 2.3, 2.2, 1.2, "Requête d'un\nagent génératif", face="#ffffff", edge=S1, fs=7.8)
    box(ax, 0.3, 0.7, 2.2, 1.1, "Cache\n(durée bornée)", face="#f2f8f4", edge=S3, fs=7.4)
    arrow(ax, (1.4, 2.25), (1.4, 1.85), color=S3, lw=1.2, style="<|-|>")

    niveaux = [("Fournisseur\nprimaire", S3), ("Fournisseur\nsecondaire", S1),
               ("Requête\nallégée", S4), ("Modèle\nlocal", S2),
               ("Réponse\npréformée", INK3)]
    x = 3.0
    for i, (t, c) in enumerate(niveaux):
        box(ax, x, 2.55, 1.75, 1.2, t, face="#ffffff", edge=c,
            lw=1.7 if i == 0 else 1.2, fs=7.4)
        if i:
            arrow(ax, (x - 0.28, 3.15), (x - 0.03, 3.15), color=INK3, lw=1.2)
            label(ax, x - 0.155, 3.98, "échec ou\ndélai dépassé", fs=5.9, color=INK3)
        x += 2.03

    # collecteur horizontal sous les niveaux, sans traverser aucune boîte
    ax.plot([3.87, 12.0], [1.95, 1.95], color=GRID, lw=1.0, zorder=1)
    for i in range(5):
        ax.plot([3.87 + i * 2.03, 3.87 + i * 2.03], [2.5, 1.95], color=GRID, lw=1.0, zorder=1)
    arrow(ax, (12.0, 1.95), (12.0, 1.6), color=S3, lw=1.3)
    box(ax, 10.85, 0.5, 2.3, 1.1, "Réponse\ntoujours produite", face="#f2f8f4", edge=S3,
        lw=1.6, fs=7.8)

    band(ax, 3.0, 4.6, 9.7, 1.05, "")
    label(ax, 7.85, 5.1,
          "Sélection par rôle : chaque agent se voit affecter le modèle adapté à sa tâche, non un modèle unique",
          fs=8.0, color=INK2, weight="bold")
    label(ax, 6.5, 0.35,
          "Budget de latence par niveau : mieux vaut une réponse un peu moins bonne à temps qu'une réponse excellente hors délai",
          fs=7.8, color=S4)
    save(fig, "fig_cascade_llm.png")


def fig_pipeline_rag():
    fig, ax = plt.subplots(figsize=(12.4, 4.8))
    blank(ax, (0, 15.5), (0, 6.0))

    band(ax, 0.2, 3.15, 3.5, 2.65, "CORPUS")
    for i, t in enumerate(["Scripts et argumentaires", "Procédures de stock",
                           "Fiches produit", "Décisions validées"]):
        box(ax, 0.45, 5.05 - i * 0.55, 3.0, 0.46, t, face="#ffffff", edge=S5, lw=1.0, fs=6.9)

    box(ax, 0.45, 1.5, 3.0, 1.35, "Découpage sémantique\n+ vectorisation\n+ métadonnées de source",
        face="#ffffff", edge=S5, fs=7.2)
    arrow(ax, (1.95, 3.1), (1.95, 2.9), color=INK3, lw=1.2)

    box(ax, 4.15, 3.9, 2.5, 1.15, "Recherche\nsémantique", face="#ffffff", edge=S1, fs=7.6)
    box(ax, 4.15, 2.3, 2.5, 1.15, "Recherche\nlexicale", face="#ffffff", edge=S1, fs=7.6)
    label(ax, 5.4, 5.32, "vocabulaire différent", fs=6.4, color=INK3)
    label(ax, 5.4, 1.95, "codes et références exacts", fs=6.4, color=INK3)
    arrow(ax, (3.5, 2.2), (4.1, 3.0), color=INK3, lw=1.2, rad=0.1)
    arrow(ax, (3.5, 2.2), (4.1, 4.4), color=INK3, lw=1.2, rad=0.15)

    box(ax, 7.1, 3.0, 2.2, 1.4, "Fusion des\nclassements\net reclassement", face="#f4f7fc",
        edge=S7, lw=1.5, fs=7.4)
    arrow(ax, (6.7, 4.4), (7.05, 4.1), color=INK3, lw=1.2)
    arrow(ax, (6.7, 2.9), (7.05, 3.3), color=INK3, lw=1.2)

    box(ax, 9.75, 3.85, 2.4, 1.1, "Seuil de\npertinence atteint ?", face="#fdf6ef", edge=S4, fs=7.4)
    arrow(ax, (9.35, 3.7), (9.7, 4.4), color=INK3, lw=1.2)

    box(ax, 12.6, 4.3, 2.7, 0.95, "Génération ancrée\navec citation des sources",
        face="#ffffff", edge=S3, lw=1.6, fs=7.4)
    box(ax, 12.6, 2.9, 2.7, 0.95, "ABSTENTION\n« rien de pertinent »", face="#f6f6f4",
        edge=INK3, lw=1.6, fs=7.4, weight="bold")
    arrow(ax, (12.2, 4.5), (12.55, 4.78), color=S3, lw=1.3)
    arrow(ax, (12.2, 4.1), (12.55, 3.38), color=INK3, lw=1.3)
    label(ax, 12.4, 4.95, "oui", fs=6.6, color=S3)
    label(ax, 12.4, 3.15, "non", fs=6.6, color=INK3)

    box(ax, 9.75, 1.35, 5.55, 1.05, "Repli lexical sur corpus local si la base vectorielle est indisponible\n"
                                    "— la recherche ne renvoie jamais vide pour cause de panne",
        face="#f2f8f4", edge=S3, lw=1.3, fs=7.2)
    arrow(ax, (8.2, 2.95), (9.7, 2.1), color=S3, lw=1.2, ls="--", rad=-0.12)
    save(fig, "fig_pipeline_rag.png")


# ═════════════════════════════════════════════════════════════════════════════
# CHAPITRE 6
# ═════════════════════════════════════════════════════════════════════════════
def fig_archi_physique():
    fig, ax = plt.subplots(figsize=(11.6, 5.6))
    blank(ax, (0, 13), (0, 7))

    band(ax, 0.3, 4.3, 3.3, 2.4, "POSTES CLIENTS")
    box(ax, 0.6, 5.55, 2.7, 0.75, "Navigateur — poste fixe", face="#ffffff", edge=S1, fs=7.4)
    box(ax, 0.6, 4.62, 2.7, 0.75, "Navigateur — mobile", face="#ffffff", edge=S1, fs=7.4)

    band(ax, 4.0, 0.4, 5.3, 6.3, "HÔTE CONTENEURISÉ")
    box(ax, 4.3, 5.15, 4.7, 1.15, "Conteneur applicatif\nservices · orchestration · agents",
        face="#ffffff", edge=S2, lw=1.7, fs=7.6)
    box(ax, 4.3, 3.75, 2.25, 1.1, "Conteneur\nPostgreSQL", face="#ffffff", edge=S4, fs=7.4)
    box(ax, 6.75, 3.75, 2.25, 1.1, "Conteneur\nRedis", face="#ffffff", edge=S4, fs=7.4)
    box(ax, 4.3, 2.35, 4.7, 1.1, "Conteneur base vectorielle", face="#ffffff", edge=S5, fs=7.4)
    box(ax, 4.3, 0.85, 4.7, 1.2, "Tâches planifiées hors chemin de requête\n"
                                 "synchronisation · prévision de base · correction · mesure de précision",
        face="#f2f8f4", edge=S3, lw=1.3, fs=7.0)

    band(ax, 9.7, 3.3, 3.0, 3.4, "SERVICES EXTERNES")
    for i, t in enumerate(["Fournisseurs de\nmodèles de langage", "Données\nmétéorologiques",
                           "Observabilité"]):
        box(ax, 9.95, 5.5 - i * 1.05, 2.5, 0.85, t, face="#ffffff", edge=S7, lw=1.1, fs=7.2)
        arrow(ax, (9.0, 5.7), (9.9, 5.92 - i * 1.05), color=GRID, lw=1.0, rad=0.05)

    band(ax, 9.7, 0.4, 3.0, 2.6, "INTÉGRATION CONTINUE")
    for i, t in enumerate(["Analyse statique", "Tests unitaires", "Tests bout en bout",
                           "Vérification de schéma"]):
        box(ax, 9.95, 2.3 - i * 0.55, 2.5, 0.46, t, face="#ffffff", edge=GRID, lw=1.0, fs=6.9)

    for y in (5.9, 4.95):
        arrow(ax, (3.65, y), (4.25, 5.7), color=INK3, lw=1.2, rad=0.05)
    arrow(ax, (6.65, 5.1), (6.65, 4.9), color=INK3, lw=1.2)
    arrow(ax, (6.65, 3.7), (6.65, 3.5), color=INK3, lw=1.2)

    label(ax, 6.5, 0.12, "Aucun secret dans le dépôt : toute configuration sensible passe par variables d'environnement",
          fs=8.0, color=INK2, weight="bold")
    save(fig, "fig_archi_physique.png")


# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Génération des schémas des chapitres 3 à 6…")
    for f in (fig_schema_relationnel, fig_pipeline_preparation, fig_decoupage_temporel,
              fig_archi_trois_tiers, fig_archi_couches, fig_composants, fig_classes_metier,
              fig_pipeline_prevision_ca, fig_pipeline_prevision_demande,
              fig_arbre_decision_stock, fig_graphe_orchestration, fig_automate_guardrail,
              fig_cycle_bon_commande, fig_cascade_llm, fig_pipeline_rag,
              fig_archi_physique):
        try:
            f()
        except Exception as e:
            print(f"  !! {f.__name__} : {type(e).__name__}: {str(e)[:150]}")
    print("Terminé.")


# ═════════════════════════════════════════════════════════════════════════════
# CHAPITRE 1 — cycle CRISP-DM annoté du plan du rapport
# ═════════════════════════════════════════════════════════════════════════════
def fig_crispdm_chapitres():
    fig, ax = plt.subplots(figsize=(9.0, 6.6))
    blank(ax, (0, 10), (0, 7.8))

    phases = [
        ("1. Compréhension\nmétier", "Chapitre 2", 5.0, 6.45),
        ("2. Compréhension\ndes données", "Chapitre 3", 8.05, 4.85),
        ("3. Préparation\ndes données", "Chapitre 3", 8.05, 2.55),
        ("4. Modélisation", "Chapitre 4", 5.0, 0.95),
        ("5. Évaluation", "Chapitre 5", 1.95, 2.55),
        ("6. Déploiement", "Chapitre 6", 1.95, 4.85),
    ]

    ax.add_patch(Circle((5.0, 3.7), 1.30, facecolor="#f2f6fd",
                        edgecolor=GRID, linewidth=1.2, zorder=1))
    label(ax, 5.0, 3.95, "DONNÉES", fs=9.5, color=S1, weight="bold")
    label(ax, 5.0, 3.48, "Retail\nOoredoo", fs=8, color=INK2)

    for nom, chap, cx, cy in phases:
        w, h = 2.45, 1.10
        box(ax, cx - w / 2, cy - h / 2, w, h, "", face="#ffffff", edge=S1, lw=1.5, radius=0.08)
        label(ax, cx, cy + 0.16, nom, fs=8.6, color=INK, weight="bold")
        ax.add_patch(FancyBboxPatch((cx - 0.75, cy - 0.46), 1.5, 0.31,
                                    boxstyle="round,pad=0,rounding_size=0.06",
                                    facecolor=S2, edgecolor=S2, zorder=3))
        label(ax, cx, cy - 0.305, chap, fs=7.6, color="#ffffff", weight="bold")

    def link(a, b, frac=0.36):
        _, _, x1, y1 = phases[a]
        _, _, x2, y2 = phases[b]
        dx, dy = x2 - x1, y2 - y1
        n = (dx ** 2 + dy ** 2) ** 0.5
        off = n * frac
        arrow(ax, (x1 + dx / n * off, y1 + dy / n * off),
              (x2 - dx / n * off, y2 - dy / n * off), color=INK3, lw=1.5)

    for i in range(6):
        link(i, (i + 1) % 6)

    arrow(ax, (3.95, 1.35), (2.95, 2.15), color=INK3, lw=1.5, ls="--", rad=0.25)
    label(ax, 3.55, 1.72, "retour", fs=6.8, color=INK3)
    arrow(ax, (7.2, 3.55), (7.2, 4.15), color=INK3, lw=1.4, ls="--", rad=-0.3)

    label(ax, 5.0, 7.55,
          "Chaque phase du cycle correspond à un chapitre du rapport ;\n"
          "chaque chapitre se ferme sur la décision qui autorise le suivant",
          fs=8.6, color=INK2, weight="bold")
    save(fig, "fig_crispdm_chapitres.png")


# ═════════════════════════════════════════════════════════════════════════════
# CHAPITRE 1 — cycles des méthodologies comparées
# ═════════════════════════════════════════════════════════════════════════════
def _cycle_lineaire(nom_fichier, etapes, sous_titre, boucle=None, col=S1):
    n = len(etapes)
    fig, ax = plt.subplots(figsize=(2.05 * n + 0.9, 2.9))
    blank(ax, (0, 2.05 * n + 0.9), (0, 3.4))
    x = 0.35
    for i, (t, sub) in enumerate(etapes):
        box(ax, x, 1.35, 1.75, 1.25, f"{t}\n\n{sub}" if sub else t,
            face="#ffffff", edge=col, lw=1.5, fs=7.6)
        if i:
            arrow(ax, (x - 0.28, 1.98), (x - 0.03, 1.98), color=INK3, lw=1.3)
        x += 2.05
    if boucle:
        arrow(ax, (x - 1.18, 1.30), (1.22, 1.30), color=INK3, lw=1.2, ls="--", rad=-0.16)
        label(ax, (x - 0.35) / 2, 0.62, boucle, fs=7.2, color=INK3)
    label(ax, (x - 0.35) / 2, 3.05, sous_titre, fs=8.4, color=INK2, weight="bold")
    save(fig, nom_fichier)


def fig_semma_cycle():
    _cycle_lineaire("semma_cycle.png", [
        ("Sample", "échantillonner"), ("Explore", "explorer"),
        ("Modify", "transformer"), ("Model", "modéliser"), ("Assess", "évaluer")],
        "SEMMA — cycle centré sur l'analyse, sans phase métier ni déploiement",
        boucle="itération sur les étapes analytiques", col=S4)


def fig_kdd_cycle():
    _cycle_lineaire("kdd_cycle.png", [
        ("Sélection", "données\ncibles"), ("Prétraitement", "nettoyage"),
        ("Transformation", "réduction"), ("Fouille", "extraction\nde motifs"),
        ("Interprétation", "connaissance")],
        "KDD — processus d'extraction de connaissances, orienté découverte",
        boucle="retours possibles entre toutes les étapes", col=S7)


def fig_gimsi_cycle():
    _cycle_lineaire("gimsi_cycle.png", [
        ("Identification", "environnement\net entreprise"),
        ("Conception", "objectifs et\nindicateurs"),
        ("Mise en œuvre", "système de\npilotage"),
        ("Amélioration", "audit et\nsuivi")],
        "GIMSI — démarche de conception d'un système de pilotage décisionnel",
        boucle="amélioration continue du dispositif", col=S5)


def fig_tdsp_cycle():
    fig, ax = plt.subplots(figsize=(8.6, 5.4))
    blank(ax, (0, 10), (0, 6.6))
    phases = [("Compréhension\nmétier", 5.0, 5.5), ("Acquisition et\ncompréhension\ndes données", 8.1, 3.6),
              ("Modélisation", 5.0, 1.4), ("Déploiement", 1.9, 3.6)]
    ax.add_patch(Circle((5.0, 3.45), 1.15, facecolor="#f2f6fd", edgecolor=GRID, lw=1.2, zorder=1))
    label(ax, 5.0, 3.62, "Acceptation", fs=8.4, color=S1, weight="bold")
    label(ax, 5.0, 3.20, "par le client", fs=7.6, color=INK2)
    for t, cx, cy in phases:
        box(ax, cx - 1.20, cy - 0.55, 2.40, 1.10, t, face="#ffffff", edge=S3, lw=1.5, fs=8.0)
    for i in range(4):
        x1, y1 = phases[i][1], phases[i][2]
        x2, y2 = phases[(i + 1) % 4][1], phases[(i + 1) % 4][2]
        dx, dy = x2 - x1, y2 - y1
        n = (dx ** 2 + dy ** 2) ** 0.5
        arrow(ax, (x1 + dx / n * 1.25, y1 + dy / n * 1.25),
              (x2 - dx / n * 1.25, y2 - dy / n * 1.25), color=INK3, lw=1.5)
    label(ax, 5.0, 6.35, "TDSP — cycle itératif orienté équipe et livraison logicielle",
          fs=8.4, color=INK2, weight="bold")
    save(fig, "tdsp_cycle.png")


def fig_crispdm_cycle():
    fig, ax = plt.subplots(figsize=(8.4, 6.2))
    blank(ax, (0, 10), (0, 7.4))
    phases = [("1. Compréhension\nmétier", 5.0, 6.15), ("2. Compréhension\ndes données", 8.0, 4.6),
              ("3. Préparation\ndes données", 8.0, 2.4), ("4. Modélisation", 5.0, 0.85),
              ("5. Évaluation", 2.0, 2.4), ("6. Déploiement", 2.0, 4.6)]
    ax.add_patch(Circle((5.0, 3.5), 1.25, facecolor="#f2f6fd", edgecolor=GRID, lw=1.2, zorder=1))
    label(ax, 5.0, 3.5, "DONNÉES", fs=9.5, color=S1, weight="bold")
    for t, cx, cy in phases:
        box(ax, cx - 1.20, cy - 0.50, 2.40, 1.00, t, face="#ffffff", edge=S1, lw=1.5, fs=8.2)
    for i in range(6):
        x1, y1 = phases[i][1], phases[i][2]
        x2, y2 = phases[(i + 1) % 6][1], phases[(i + 1) % 6][2]
        dx, dy = x2 - x1, y2 - y1
        n = (dx ** 2 + dy ** 2) ** 0.5
        arrow(ax, (x1 + dx / n * 1.18, y1 + dy / n * 1.18),
              (x2 - dx / n * 1.18, y2 - dy / n * 1.18), color=INK3, lw=1.5)
    arrow(ax, (3.95, 1.25), (2.95, 2.05), color=INK3, lw=1.4, ls="--", rad=0.25)
    arrow(ax, (7.15, 3.35), (7.15, 3.95), color=INK3, lw=1.4, ls="--", rad=-0.3)
    label(ax, 5.0, 7.15, "CRISP-DM — six phases, retours explicites entre phases",
          fs=8.4, color=INK2, weight="bold")
    save(fig, "crispdm_cycle.png")
