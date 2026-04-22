class ASCBaseError(Exception):
    """Base exception pour tout le projet."""
    pass


class StockGuardError(ASCBaseError):
    """Levée si APP07 tente de générer un conseil sans lire le stock."""
    def __init__(self, sku: str):
        super().__init__(
            f"Stock guard violation: product '{sku}' "
            f"must be checked before generating advice."
        )


class AgentTimeoutError(ASCBaseError):
    """Levée si un agent dépasse son timeout."""
    def __init__(self, agent_id: str, timeout_s: int):
        super().__init__(
            f"Agent '{agent_id}' timed out after {timeout_s}s."
        )


class MCPServerError(ASCBaseError):
    """Levée si un MCP Server ne répond pas."""
    def __init__(self, server: str, tool: str, reason: str):
        super().__init__(
            f"MCP server '{server}' tool '{tool}' failed: {reason}"
        )


class KafkaConsumerError(ASCBaseError):
    pass


class VLLMGenerationError(ASCBaseError):
    pass


class TimesFMError(ASCBaseError):
    pass


class MilvusSearchError(ASCBaseError):
    pass