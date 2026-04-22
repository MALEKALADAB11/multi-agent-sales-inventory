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

    def cycle_end(self, state: dict):
        elapsed = (time.time() - self._t_cycle) * 1000
        urgence = state.get("niveau_urgence", "?")
        gap     = state.get("ecart_objectif", 0)
        eod     = state.get("forecast_eod", 0)

        urg_color = (
            RED    if urgence == "HIGH"   else
            YELLOW if urgence == "MEDIUM" else
            GREEN
        )

        print(f"{DIM}{_line()}{RESET}")
        print(_box(f"CYCLE END  {elapsed:.0f}ms", CYAN))
        print(
            f"  {DIM}urgence={RESET}{BOLD}{urg_color}{urgence}{RESET}"
            f"  {DIM}gap={RESET}{BOLD}{RED}{gap:.1f}%{RESET}"
            f"  {DIM}eod={RESET}{BOLD}{GREEN}{eod:,.0f} DT{RESET}"
            f"  {DIM}nodes={RESET}{WHITE}{self._node_count}{RESET}"
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

        # Inputs
        for k, v in inputs.items():
            if isinstance(v, float):
                v_str = f"{v:,.2f}"
            elif isinstance(v, int):
                v_str = f"{v:,}"
            else:
                v_str = str(v)[:60]
            print(f"       {DIM}→ {k}={RESET}{WHITE}{v_str}{RESET}")

        # Outputs
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

            # Colorer les valeurs importantes
            if k == "niveau_urgence":
                val_color = (
                    RED    if v == "HIGH"   else
                    YELLOW if v == "MEDIUM" else
                    GREEN
                )
                v_str = f"{BOLD}{val_color}{v}{RESET}"
            elif k in ("ecart_objectif", "forecast_eod", "forecast_mape"):
                v_str = f"{BOLD}{WHITE}{v_str}{RESET}"
            else:
                v_str = f"{DIM}{v_str}{RESET}"

            print(f"       {PURPLE}{k}{RESET} = {v_str}")

    # ── Router decision ───────────────────────────────

    def router_decision(self, from_node: str, to_node: str, reason: str = ""):
        icon = "⟶" if to_node != "END" else "⊠"
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