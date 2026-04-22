import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ObservabilityTools:

    def __init__(self):
        self.traces = {}

    async def start_trace(
        self,
        agent_id: str,
        cycle_id: str,
        store_id: str
    ) -> str:
        trace_id = f"{agent_id}-{cycle_id}-{datetime.utcnow().timestamp()}"
        self.traces[trace_id] = {
            "agent_id":  agent_id,
            "cycle_id":  cycle_id,
            "store_id":  store_id,
            "started_at": datetime.utcnow(),
            "success":   None
        }
        logger.info("TRACE START — agent=%s cycle=%s", agent_id, cycle_id)
        return trace_id

    async def end_trace(
        self,
        trace_id: str,
        success:  bool,
        error:    str = None
    ) -> None:
        if trace_id in self.traces:
            t = self.traces[trace_id]
            t["success"]    = success
            t["error"]      = error
            t["ended_at"]   = datetime.utcnow()
            duration = (t["ended_at"] - t["started_at"]).total_seconds() * 1000
            t["duration_ms"] = round(duration, 2)
            logger.info(
                "TRACE END — agent=%s success=%s duration=%.0fms",
                t["agent_id"], success, duration
            )

    async def trace_agent_call(
        self,
        agent_id: str,
        tool:     str,
        input_data: dict = None,
        output_data: dict = None,
        latency_ms: float = 0
    ) -> None:
        logger.info(
            "MCP CALL — agent=%s tool=%s latency=%.0fms",
            agent_id, tool, latency_ms
        )

    async def get_agent_health(self) -> dict:
        return {
            "APP02": {"status": "ok", "latency_p95": 450},
            "APP05": {"status": "ok", "latency_p95": 320},
            "APP07": {"status": "ok", "latency_p95": 1800},
            "APP08": {"status": "ok", "latency_p95": 120},
            "APP09": {"status": "ok", "latency_p95": 1200},
        }

    async def log_cycle_metric(self, cycle_id: str, metrics: dict) -> None:
        logger.info("CYCLE METRIC — cycle=%s metrics=%s", cycle_id, metrics)