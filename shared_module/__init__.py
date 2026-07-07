# Shared Module Initialization
"""
shared_module — Module partagé entre sales, inventory et monitoring.
Fournit : config, db, agent_logger
"""

from .config import config, get_config, Config
from .db import (
    get_conn, get_raw_conn, get_async_pool,
    get_sales_conn, get_inventory_conn, get_monitoring_conn,
    safe_json, fetchall, fetchone, execute, execute_returning,
)
from .agent_logger import (
    AgentLogger,
    log_node_start, log_node_complete, log_node_error,
    log_cycle, log_rag_feedback, log_inventory_action,
    get_recent_cycles, get_recent_errors, get_agent_stats, resolve_error,
)

__all__ = [
    # Config
    "config", "get_config", "Config",
    # DB
    "get_conn", "get_raw_conn", "get_async_pool",
    "get_sales_conn", "get_inventory_conn", "get_monitoring_conn",
    "safe_json", "fetchall", "fetchone",
    "execute", "execute_returning",
    # Logger
    "AgentLogger",
    "log_node_start", "log_node_complete", "log_node_error",
    "log_cycle", "log_rag_feedback", "log_inventory_action",
    "get_recent_cycles", "get_recent_errors", "get_agent_stats",
    "resolve_error",
]
