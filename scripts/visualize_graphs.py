"""
visualize_graphs.py
====================
Standalone script — dumps a PNG for every compiled LangGraph in the project,
exactly as they exist today. No refactor, no code changes elsewhere.

WHERE TO PUT THIS FILE:
  Anywhere your Python path can already resolve `app.inventory...` imports —
  i.e. wherever you'd normally run a script from inside this project.
  A `scripts/` folder at the repo root works well:

      your-repo/
        app/
          inventory/...
        scripts/
          visualize_graphs.py   <- put it here

HOW TO RUN IT:
  From the repo root, same environment/venv the app itself runs in:

      python scripts/visualize_graphs.py

  It needs your app's normal Python environment (langgraph, langchain_core,
  and anything agent.py imports) — run it the same way you'd run any other
  script in this project, not in a fresh empty venv.

WHAT IT PRODUCES:
  A `graphs/` folder next to wherever you run it from, containing:
    analysis_agent.png
    context_agent.png
    decision_agent.png
    supervisor.png   (only if the supervisor's branch functions are importable —
                       see note at the bottom)
"""

import os

OUT_DIR = "graphs"
os.makedirs(OUT_DIR, exist_ok=True)


def _save(graph, filename: str):
    png_bytes = graph.get_graph().draw_mermaid_png()
    path = os.path.join(OUT_DIR, filename)
    with open(path, "wb") as f:
        f.write(png_bytes)
    print(f"wrote {path}")


# ── Analysis agent ────────────────────────────────────────────────────────
from app.inventory.agents.analysis.agent import create_analysis_agent
analysis_agent = create_analysis_agent(use_llm=False)   # use_llm=False avoids needing a live API key just to draw the graph
_save(analysis_agent.graph, "analysis_agent.png")


# ── Context agent ─────────────────────────────────────────────────────────
from app.inventory.agents.context.agent import create_context_agent
context_agent = create_context_agent(use_llm=False)
_save(context_agent.graph, "context_agent.png")


# ── Decision agent ────────────────────────────────────────────────────────
from app.inventory.agents.decision.agent import create_decision_agent
decision_agent = create_decision_agent(use_llm=False)
_save(decision_agent.graph, "decision_agent.png")


# ── Supervisor ────────────────────────────────────────────────────────────
# get_or_build_supervisor() needs real branch functions (inventory_branch_fn,
# sales_branch_fn, knowledge_branch_fn, coach_fusion_fn, guardrail_fn) passed
# in — wherever your app currently calls get_or_build_supervisor(...) for
# real (likely in a router or startup file), copy that same call here so
# this script builds it the same way. Example shape:
#
# from app.sales.orchestration.supervisor_agent import get_supervisor_graph
# supervisor_graph = get_supervisor_graph()
# _save(supervisor_graph, "supervisor.png")
#
# Left commented out here because I haven't seen where your app assembles
# those five callables — fill in the real import/call your app already uses.
