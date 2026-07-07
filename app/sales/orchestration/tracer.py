import time
import logging
from datetime import datetime
from typing import Any

# ── Couleurs ANSI ─────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

BLACK  = "\033[30m"
RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
BLUE   = "\033[34m"
PURPLE = "\033[35m"
CYAN   = "\033[36m"
WHITE  = "\033[37m"

BG_BLACK  = "\033[40m"
BG_PURPLE = "\033[45m"
BG_CYAN   = "\033[46m"
BG_BLUE   = "\033[44m"


def _line(char: str = "─", width: int = 64) -> str:
    return char * width


def _box(text: str, color: str = PURPLE, width: int = 64) -> str:
    pad   = (width - len(text) - 2) // 2
    inner = f"{'─' * pad} {text} {'─' * (width - pad - len(text) - 2)}"
    return f"{BOLD}{color}{inner}{RESET}"


class CycleTracer:
    """
    Affiche le cycle LangGraph dans le terminal.
    Montre : nodes, tools appelés, state écrit, timings.

    cycle_end reçoit les valeurs directement via kwargs nommés
    (urgency=, gap=, eod=, nodes=) tels que passés par graph.py —
    state n'est jamais passé directement ici.
    """

    def __init__(self, show_state: bool = True):
        self.show_state  = show_state
        self._t_cycle    = 0.0
        self._t_node     = 0.0
        self._node_count = 0

    # ── Cycle ─────────────────────────────────────────

    def cycle_start(self, cycle_id: str, store_id: str, trigger: str):
        self._t_cycle    = time.time()
        self._node_count = 0
        now = datetime.now().strftime("%H:%M:%S")

        print()
        print(_box(f"CYCLE START  {cycle_id}", PURPLE))
        print(
            f"{DIM}  store={WHITE}{store_id}{RESET}"
            f"{DIM}  trigger={WHITE}{trigger}{RESET}"
            f"{DIM}  time={WHITE}{now}{RESET}"
        )
        print(f"{DIM}{_line()}{RESET}")

    def cycle_end(
        self,
        cycle_id:  str   = None,
        total_ms:  float = None,
        nodes:     int   = None,
        urgency:   str   = None,
        gap:       float = None,
        eod:       float = None,
        **kwargs,
    ):
        """
        Appelé par graph.py avec :
            tracer.cycle_end(cycle_id, total_ms, nodes=..., urgency=..., gap=..., eod=...)

        Tous les champs viennent en kwargs nommés depuis final_state —
        jamais via un objet state brut pour éviter le NoneType crash.
        """
        elapsed = (time.time() - self._t_cycle) * 1000

        # Valeurs directement depuis les kwargs — aucun .get() sur un objet potentiellement None
        urgence   = urgency   if urgency  is not None else "?"
        gap_val   = gap       if gap      is not None else 0.0
        eod_val   = eod       if eod      is not None else 0.0
        nodes_val = nodes     if nodes    is not None else self._node_count

        urg_color = (
            RED    if urgence == "CRITICAL" else
            RED    if urgence == "HIGH"     else
            YELLOW if urgence == "MEDIUM"   else
            GREEN
        )

        print(f"{DIM}{_line()}{RESET}")
        print(_box(f"CYCLE END  {elapsed:.0f}ms", CYAN))
        print(
            f"  {DIM}urgence={RESET}{BOLD}{urg_color}{urgence}{RESET}"
            f"  {DIM}gap={RESET}{BOLD}{RED}{gap_val:.1f}%{RESET}"
            f"  {DIM}eod={RESET}{BOLD}{GREEN}{eod_val:,.0f} DT{RESET}"
            f"  {DIM}nodes={RESET}{WHITE}{nodes_val}{RESET}"
        )
        print()

    # ── Node ──────────────────────────────────────────

    def node_start(self, node_id: str, description: str = ""):
        self._t_node     = time.time()
        self._node_count += 1

        print()
        print(
            f"  {BOLD}{BLUE}▶ NODE  {node_id}{RESET}"
            f"  {DIM}{description}{RESET}"
        )
        print(f"  {DIM}{_line('·', 58)}{RESET}")

    def node_end(self, node_id: str, status: str = "ok"):
        elapsed = (time.time() - self._t_node) * 1000
        icon    = f"{GREEN}✓{RESET}" if status == "ok" else f"{RED}✗{RESET}"
        print(
            f"  {icon} {DIM}{node_id} done "
            f"in {WHITE}{elapsed:.0f}ms{RESET}"
        )

    # ── Tool call ─────────────────────────────────────

    def tool_call(self, tool: str, inputs: dict, outputs: dict = None):
        """Affiche un appel de tool avec ses inputs et outputs."""
        print(
            f"    {CYAN}⚙  tool:{RESET} {BOLD}{tool}{RESET}"
        )

        for k, v in inputs.items():
            if isinstance(v, float):
                v_str = f"{v:,.2f}"
            elif isinstance(v, int):
                v_str = f"{v:,}"
            else:
                v_str = str(v)[:60]
            print(f"       {DIM}→ {k}={RESET}{WHITE}{v_str}{RESET}")

        if outputs:
            for k, v in outputs.items():
                if isinstance(v, float):
                    v_str = f"{v:,.2f}"
                elif isinstance(v, int):
                    v_str = f"{v:,}"
                else:
                    v_str = str(v)[:60]
                print(
                    f"       {DIM}← {k}={RESET}"
                    f"{GREEN}{v_str}{RESET}"
                )

    # ── State write ───────────────────────────────────

    def state_write(self, fields: dict):
        """Affiche les champs écrits dans le State LangGraph."""
        print(f"    {PURPLE}◈  state written:{RESET}")
        for k, v in fields.items():
            if isinstance(v, float):
                v_str = f"{v:,.2f}"
            elif isinstance(v, dict):
                v_str = f"{{...{len(v)} keys}}"
            else:
                v_str = str(v)[:60]

            if k == "urgency_level":
                val_color = (
                    RED    if v in ("HIGH", "CRITICAL") else
                    YELLOW if v == "MEDIUM"             else
                    GREEN
                )
                v_str = f"{BOLD}{val_color}{v}{RESET}"
            elif k in ("gap_objectif", "forecast_eod", "forecast_mape", "gap_amount", "attainment"):
                v_str = f"{BOLD}{WHITE}{v_str}{RESET}"
            else:
                v_str = f"{DIM}{v_str}{RESET}"

            print(f"       {PURPLE}{k}{RESET} = {v_str}")

    # ── Router decision ───────────────────────────────

    def router_decision(self, from_node: str, to_node: str, reason: str = ""):
        icon  = "⟶" if to_node != "END" else "⊠"
        color = YELLOW if to_node != "END" else DIM
        print(
            f"    {color}{icon}  router:{RESET}"
            f"  {from_node} → {BOLD}{to_node}{RESET}"
            f"  {DIM}{reason}{RESET}"
        )

    # ── Step interne ──────────────────────────────────

    def step(self, number: int, label: str):
        print(
            f"    {DIM}[{number}]{RESET}"
            f" {WHITE}{label}{RESET}"
        )

    # ── Error ─────────────────────────────────────────

    def error(self, node: str, message: str):
        print(
            f"    {BOLD}{RED}✗ ERROR in {node}:{RESET}"
            f" {message}"
        )
