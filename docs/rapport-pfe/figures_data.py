"""
figures_data.py — Figures d'exploration du chapitre 3, générées depuis la base réelle.

Contrairement à figures.py (schémas conceptuels dessinés), ce script interroge
directement PostgreSQL : toute valeur affichée provient des données du projet.

Palette et conventions identiques à figures.py.
Sortie : docs/rapport/img/*.png  (200 dpi, fond clair, sans titre incrusté).

Usage : python docs/rapport/figures_data.py
"""
from __future__ import annotations

import os
import sys
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
import psycopg2

load_dotenv()

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img")
os.makedirs(OUT, exist_ok=True)

# ── Palette (identique à figures.py) ─────────────────────────────────────────
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

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ooredoo_sales"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=200, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    print("  ->", name)


def grid(ax, axis="y"):
    ax.grid(axis=axis, color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


# ════════════════════════════════════════════════════════════════════════════
# Fig. 3.x — Profil intra-journalier du chiffre d'affaires
# ════════════════════════════════════════════════════════════════════════════
def fig_profil_horaire(cx):
    df = pd.read_sql("""
        SELECT heure,
               SUM(lig_ttc)                    AS ca,
               COUNT(DISTINCT date_only)       AS jours
        FROM sales.transactions
        WHERE heure BETWEEN 8 AND 20
        GROUP BY heure
        ORDER BY heure
    """, cx)
    df["ca_moyen"] = df["ca"] / df["jours"]

    # profil par jour de semaine, pour montrer la dispersion
    dj = pd.read_sql("""
        SELECT heure,
               EXTRACT(ISODOW FROM transaction_date)::int AS dow,
               SUM(lig_ttc)              AS ca,
               COUNT(DISTINCT date_only) AS jours
        FROM sales.transactions
        WHERE heure BETWEEN 8 AND 20
        GROUP BY 1, 2
    """, cx)
    dj["ca_moyen"] = dj["ca"] / dj["jours"]

    fig, ax = plt.subplots(figsize=(9.6, 3.9))
    grid(ax)

    for d in range(1, 8):
        s = dj[dj.dow == d].sort_values("heure")
        ax.plot(s.heure, s.ca_moyen, color=INK3, lw=0.9, alpha=0.45, zorder=2)

    ax.plot(df.heure, df.ca_moyen, color=S1, lw=2.6, marker="o", ms=4.5,
            zorder=3, label="Profil moyen toutes journées")
    ax.plot([], [], color=INK3, lw=0.9, alpha=0.6, label="Profil par jour de semaine")

    pic = df.loc[df.ca_moyen.idxmax()]
    ax.annotate(f"pic à {int(pic.heure)} h",
                xy=(pic.heure, pic.ca_moyen), xytext=(pic.heure + 1.1, pic.ca_moyen * 1.02),
                color=S2, fontsize=8.5,
                arrowprops=dict(arrowstyle="->", color=S2, lw=1.1))

    ax.set_xlabel("Heure de la journée")
    ax.set_ylabel("Chiffre d'affaires moyen (TND)")
    ax.set_xticks(range(8, 21))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", " ")))
    ax.legend(frameon=False, fontsize=8.5, loc="upper left")
    save(fig, "fig_profil_horaire.png")


# ════════════════════════════════════════════════════════════════════════════
# Fig. 3.x — Saisonnalité hebdomadaire
# ════════════════════════════════════════════════════════════════════════════
def fig_saisonnalite_hebdo(cx):
    df = pd.read_sql("""
        SELECT date_only,
               EXTRACT(ISODOW FROM date_only)::int AS dow,
               SUM(lig_ttc) AS ca
        FROM sales.transactions
        GROUP BY 1, 2
    """, cx)

    data = [df[df.dow == d].ca.values for d in range(1, 8)]
    med = [np.median(v) for v in data]

    fig, ax = plt.subplots(figsize=(9.6, 3.9))
    grid(ax)

    bp = ax.boxplot(data, patch_artist=True, widths=0.55, showfliers=False, zorder=3)
    for i, box in enumerate(bp["boxes"]):
        box.set(facecolor=S1 if i < 5 else S4, alpha=0.55, edgecolor=INK2, lw=0.9)
    for elem in ("whiskers", "caps"):
        for it in bp[elem]:
            it.set(color=INK2, lw=0.9)
    for m in bp["medians"]:
        m.set(color=INK, lw=1.6)

    ax.plot(range(1, 8), med, color=S2, lw=1.4, ls="--", marker="D", ms=4,
            zorder=4, label="Médiane")

    i_min, i_max = int(np.argmin(med)), int(np.argmax(med))
    ecart = (med[i_max] - med[i_min]) / med[i_min] * 100
    ax.annotate(f"écart {JOURS[i_min].lower()} → {JOURS[i_max].lower()} : +{ecart:.0f} %",
                xy=(0.5, 0.95), xycoords="axes fraction", ha="center", va="top",
                fontsize=8.5, color=S2)

    ax.set_xticklabels([j[:3] for j in JOURS])
    ax.set_xlabel("Jour de la semaine")
    ax.set_ylabel("Chiffre d'affaires journalier (TND)")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", " ")))
    ax.legend(frameon=False, fontsize=8.5, loc="upper left")
    save(fig, "fig_saisonnalite_hebdo.png")


# ════════════════════════════════════════════════════════════════════════════
# Fig. 3.x — Concentration du catalogue (loi de Pareto)
# ════════════════════════════════════════════════════════════════════════════
def fig_pareto_references(cx):
    df = pd.read_sql("""
        SELECT sku, SUM(quantity) AS q
        FROM sales.transactions
        GROUP BY sku
        HAVING SUM(quantity) > 0
        ORDER BY q DESC
    """, cx)

    n = len(df)
    part_ref = np.arange(1, n + 1) / n * 100
    part_vol = df.q.cumsum().values / df.q.sum() * 100

    def seuil(p):
        i = int(np.searchsorted(part_vol, p))
        return part_ref[min(i, n - 1)]

    r80, r95 = seuil(80), seuil(95)

    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    grid(ax, axis="both")

    ax.plot(part_ref, part_vol, color=S1, lw=2.4, zorder=4)
    ax.fill_between(part_ref, 0, part_vol, color=S1, alpha=0.10, zorder=2)
    ax.plot([0, 100], [0, 100], color=INK3, lw=1.0, ls=":", zorder=3,
            label="Répartition uniforme (référence)")

    for r, p, col in ((r80, 80, S2), (r95, 95, S4)):
        ax.plot([r, r], [0, p], color=col, lw=1.1, ls="--", zorder=5)
        ax.plot([0, r], [p, p], color=col, lw=1.1, ls="--", zorder=5)
        ax.scatter([r], [p], color=col, s=28, zorder=6)
        ax.annotate(f"{r:.1f} % des références\n= {p} % du volume",
                    xy=(r, p), xytext=(r + 6, p - 16), color=col, fontsize=8.5,
                    arrowprops=dict(arrowstyle="->", color=col, lw=1.0))

    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_xlabel(f"Part cumulée des références, triées par volume décroissant (%) — {n:,} références"
                  .replace(",", " "))
    ax.set_ylabel("Part cumulée du volume vendu (%)")
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    save(fig, "fig_pareto_references.png")


# ════════════════════════════════════════════════════════════════════════════
# Fig. 3.x — Distribution de la couverture de stock
# ════════════════════════════════════════════════════════════════════════════
def fig_distribution_couverture(cx):
    df = pd.read_sql("""
        WITH d AS (
            SELECT sku, store_id, AVG(quantity_sold) AS dj
            FROM inventory.sales_history
            WHERE record_date >= CURRENT_DATE - INTERVAL '90 days'
            GROUP BY sku, store_id
            HAVING AVG(quantity_sold) > 0
        )
        SELECT s.quantity::float / d.dj AS couverture
        FROM inventory.stock_levels s
        JOIN d ON d.sku = s.sku AND d.store_id = s.store_id
        WHERE s.quantity >= 0
    """, cx)

    cov = df.couverture.clip(upper=90)
    if len(cov) < 20:
        print("  !! trop peu de couples stock/demande — figure non générée")
        return

    fig, ax = plt.subplots(figsize=(8.6, 4.0))
    grid(ax)

    bins = np.linspace(0, 90, 46)
    n, edges, patches = ax.hist(cov, bins=bins, zorder=3, edgecolor=SURFACE, lw=0.6)

    # coloration par zone de risque
    for p, e in zip(patches, edges[:-1]):
        if e < 10:
            p.set_facecolor(CRIT)
        elif e < 20:
            p.set_facecolor(WARN)
        elif e < 45:
            p.set_facecolor(GOOD)
        else:
            p.set_facecolor(S4)

    med = float(cov.median())
    ax.axvline(med, color=INK, lw=1.4, ls="--", zorder=5)
    ax.annotate(f"médiane {med:.0f} j", xy=(med, ax.get_ylim()[1] * 0.92),
                xytext=(med + 3, ax.get_ylim()[1] * 0.92), fontsize=8.5, color=INK)

    from matplotlib.patches import Patch
    ax.legend(handles=[
        Patch(facecolor=CRIT, label="Rupture / risque critique (< 10 j)"),
        Patch(facecolor=WARN, label="Risque élevé (10–20 j)"),
        Patch(facecolor=GOOD, label="Sain (20–45 j)"),
        Patch(facecolor=S4, label="Surstock (> 45 j)"),
    ], frameon=False, fontsize=8.2, loc="upper right")

    ax.set_xlabel(f"Couverture de stock, en jours de demande — {len(cov):,} couples référence × point de vente"
                  .replace(",", " "))
    ax.set_ylabel("Nombre de couples")
    save(fig, "fig_distribution_couverture.png")


# ════════════════════════════════════════════════════════════════════════════
# Fig. 3.x — Matrice de corrélation entre domaines
# ════════════════════════════════════════════════════════════════════════════
def fig_matrice_correlation(cx):
    df = pd.read_sql("""
        WITH v AS (
            SELECT t.sku,
                   SUM(t.lig_ttc)                       AS ca,
                   SUM(t.quantity)                      AS volume,
                   AVG(NULLIF(t.marge, 0))              AS marge,
                   AVG(t.prix_unitaire)                 AS prix
            FROM sales.transactions t
            WHERE t.transaction_date >= CURRENT_DATE - INTERVAL '180 days'
            GROUP BY t.sku
            HAVING SUM(t.quantity) > 0
        ),
        s AS (
            SELECT sku, SUM(quantity) AS stock
            FROM inventory.stock_levels
            GROUP BY sku
        )
        SELECT v.ca, v.volume, v.marge, v.prix, COALESCE(s.stock, 0) AS stock
        FROM v LEFT JOIN s ON s.sku = v.sku
    """, cx)

    if len(df) < 30:
        print("  !! trop peu de références — figure non générée")
        return

    # log pour les grandeurs très asymétriques, puis corrélation de rang
    cols = {
        "ca": "CA produit",
        "volume": "Volume vendu",
        "marge": "Marge unitaire",
        "prix": "Prix unitaire",
        "stock": "Stock disponible",
    }
    m = df[list(cols)].corr(method="spearman")
    m.index = [cols[c] for c in m.index]
    m.columns = [cols[c] for c in m.columns]

    fig, ax = plt.subplots(figsize=(6.6, 5.4))
    im = ax.imshow(m.values, cmap="RdBu_r", vmin=-1, vmax=1)

    ax.set_xticks(range(len(m)))
    ax.set_yticks(range(len(m)))
    ax.set_xticklabels(m.columns, rotation=35, ha="right", fontsize=8.5)
    ax.set_yticklabels(m.index, fontsize=8.5)

    for i in range(len(m)):
        for j in range(len(m)):
            v = m.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8.6,
                    color="white" if abs(v) > 0.55 else INK,
                    fontweight="bold" if i != j and abs(v) > 0.5 else "normal")

    # encadrer la case décisive : stock × volume
    from matplotlib.patches import Rectangle
    ivol = list(m.index).index("Volume vendu")
    isto = list(m.index).index("Stock disponible")
    ax.add_patch(Rectangle((isto - 0.5, ivol - 0.5), 1, 1,
                           fill=False, edgecolor=S2, lw=2.2))

    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label("Corrélation de rang (Spearman)", fontsize=8.5)
    cb.outline.set_visible(False)

    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks(np.arange(-0.5, len(m), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(m), 1), minor=True)
    ax.grid(which="minor", color=SURFACE, lw=2)
    ax.tick_params(which="minor", length=0)

    save(fig, "fig_matrice_correlation.png")


# ════════════════════════════════════════════════════════════════════════════
# Fig. 5.x — Décisions approuvées / rejetées par boucle
# ════════════════════════════════════════════════════════════════════════════
def fig_feedback_boucles(cx):
    df = pd.read_sql("""
        SELECT source,
               CASE WHEN decision IN ('approved', 'followed', 'accepted') THEN 'positif'
                    ELSE 'negatif' END AS issue,
               COUNT(*) AS n
        FROM public.agent_feedback
        WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
        GROUP BY 1, 2
    """, cx)

    if df.empty:
        print("  !! aucun feedback sur 30 jours — figure non générée")
        return

    libelles = {
        "incitation": "Incitations coach",
        "hitl": "Stratégies soumises\nà validation",
        "po": "Bons de commande",
        "reco": "Recommandations\nde réapprovisionnement",
    }
    piv = df.pivot(index="source", columns="issue", values="n").fillna(0)
    piv = piv.reindex([s for s in ("incitation", "hitl", "po", "reco") if s in piv.index])
    app = piv.get("positif", pd.Series(0.0, index=piv.index))
    rej = piv.get("negatif", pd.Series(0.0, index=piv.index))
    piv.index = [libelles.get(s, s) for s in piv.index]

    x = np.arange(len(piv))
    w = 0.38

    fig, ax = plt.subplots(figsize=(9.4, 3.9))
    grid(ax)
    ax.bar(x - w / 2, app.values, w, color=S1, label="Approuvées / suivies", zorder=3)
    ax.bar(x + w / 2, rej.values, w, color=S2, label="Rejetées / ignorées", zorder=3)

    hmax = max(float(app.max()), float(rej.max())) or 1.0
    for i, (a, r) in enumerate(zip(app.values, rej.values)):
        tot = a + r
        if tot:
            ax.text(i, max(a, r) + hmax * 0.05,
                    f"{a / tot * 100:.0f} % accepté", ha="center", fontsize=8.2, color=INK2)

    ax.set_ylim(0, hmax * 1.22)
    ax.set_xticks(x)
    ax.set_xticklabels(piv.index, fontsize=8.5)
    ax.set_ylabel("Nombre de décisions")
    ax.legend(frameon=False, fontsize=8.5)
    save(fig, "fig_feedback_boucles.png")


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Génération des figures de données (chapitres 3 et 5)…")
    cx = conn()
    for f in (fig_profil_horaire, fig_saisonnalite_hebdo, fig_pareto_references,
              fig_distribution_couverture, fig_matrice_correlation, fig_feedback_boucles):
        try:
            f(cx)
        except Exception as e:
            print(f"  !! {f.__name__} a échoué : {type(e).__name__}: {str(e)[:160]}")
    cx.close()
    print("Terminé.")
