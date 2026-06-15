---
project_name: 'PFE-Backend'
user_name: 'Malek'
date: '2026-06-08'
sections_completed: []
existing_patterns_found: 8
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

### Core Framework & Server
- **FastAPI**: 0.115.6 (async web framework)
- **Uvicorn**: 0.32.1 with [standard] extras
- **Starlette**: 0.41.3 (ASGI middleware)
- **Pydantic**: 2.10.3 (with pydantic-core 2.27.1, pydantic-settings 2.6.1)

### AI/ML & Agent Orchestration
- **LangChain**: 0.3.7 (LLM framework)
- **LangChain-Core**: 0.3.29
- **LangGraph**: 0.2.45 (agent state machines)
- **LangGraph-Checkpoint**: 2.0.10
- **LangGraph-SDK**: 0.1.48
- **LangChain-Groq**: 0.2.3 (LLM provider)
- **LangChain-OpenAI**: 0.2.9 (LLM provider)
- **LangChain-OLLAMA**: (local inference engine)

### Vector Database & RAG
- **Milvus**: Vector store for RAG context (coaching_scripts collection)
- **LangChain-Text-Splitters**: 0.3.3

### Data Storage & Persistence
- **SQLAlchemy**: 2.0.36 [asyncio] (async ORM)
- **asyncpg**: 0.30.0 (PostgreSQL async driver)
- **Alembic**: 1.14.0 (database migrations)
- **greenlet**: 3.1.1 (async concurrency)

### Caching & Messaging
- **Redis**: 5.2.0 [hiredis] (with hiredis 3.0.0)
- **Aiokafka**: 0.11.0 (async Kafka client)

### Protocol Support
- **MCP**: 1.1.2 (Model Context Protocol)
- **SSE-Starlette**: 2.1.3 (Server-Sent Events)
- **nest-asyncio**: 1.6.0

### HTTP & Networking
- **httpx**: 0.27.2 (async HTTP client)
- **httpcore**: 1.0.5
- **aiohttp**: 3.11.10 (async HTTP)
- **requests**: 2.32.3 (sync HTTP, legacy support)

### Testing & Development
- **pytest**: 8.1.1
- **pytest-asyncio**: 0.23.6
- **pytest-cov**: 5.0.0

### Utilities
- **python-dotenv**: 1.0.1 (environment configuration)
- **tenacity**: 9.0.0 (retry library)
- **langsmith**: 0.1.147 (observability)
- **jsonpatch**: 1.33

---

## Critical Implementation Rules

### I. Async/Await Architecture (MANDATORY)

**Rule:** All node functions, service methods, and data access must be async.

- ✅ **DO:** `async def node_fetch_context(state: SalesAgentState) -> dict:`
- ✅ **DO:** `await fetch_full_context(store_id)`
- ❌ **DON'T:** Use synchronous blocking operations inside async functions
- ❌ **DON'T:** Mix sync and async without explicit adaptation layer

**Examples from codebase:**
```python
# CORRECT: async node with await
async def node_fetch_context(state: SalesAgentState) -> dict:
    context = await fetch_full_context(sid)
    return {...state, "external_context": context}

# INCORRECT: blocking I/O in async function
async def node_fetch_context(state: SalesAgentState) -> dict:
    context = requests.get(url)  # WRONG: blocks event loop
    return {...state, "external_context": context}
```

### II. State Management Pattern (LangGraph TypedDict)

**Rule:** State is immutable and always returned as a new dict merge with existing state.

- ✅ **DO:** `return {...state, "new_field": value, "metrics": _update_metrics(state, ...)`
- ❌ **DON'T:** Mutate state: `state["field"] = value; return state`
- ❌ **DON'T:** Return None from nodes; always return updated state dict

**State fields are defined in `SalesAgentState` TypedDict** (see [sales-module/core/state.py](sales-module/core/state.py)):
- POS data flow: `pos_data`, `pos_history`, `current_hour`
- Analysis results: `gap_objectif`, `urgency_level`, `forecast_eod`, `analyst_output`
- Strategy results: `strategie`, `strategie_actions`, `focus_produits`
- Context: `external_context`, `rag_context`, `context_factors`
- Metrics: `metrics` (dict with timing data and node counts)
- Logs: `agent_logs` (list of execution records)

**Examples:**
```python
# CORRECT: return merged state dict
def _update_metrics(state: dict, key: str, value) -> dict:
    metrics = dict(state.get("metrics") or {})
    metrics[key] = value
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1
    return metrics

# Use in nodes:
output = {**state, "external_context": context}
return {**output, "metrics": _update_metrics(state, "stratege_context_ms", round(duration))}
```

### III. Node Execution Pattern

**Rule:** Every node follows this structure:
1. Extract cycle_id, store_id using `_cycle_id()` and `_store_id()` helpers
2. Create AgentLogger with node context
3. Record start time and log node execution
4. Execute business logic with try/except
5. Calculate duration and update metrics
6. Return updated state dict with metrics

**Example from [sales-module/modules/coaching/agents/stratege/nodes.py](sales-module/modules/coaching/agents/stratege/nodes.py):**
```python
async def node_fetch_context(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("fetch_context", state)
    t0 = time.time()
    
    try:
        context = await fetch_full_context(sid)
        duration = (time.time() - t0) * 1000
        output = {**state, "external_context": context}
        log.node_done("fetch_context", log_id, output, duration, {...})
        return {**output, "metrics": _update_metrics(state, "stratege_context_ms", round(duration))}
    except Exception as e:
        duration = (time.time() - t0) * 1000
        log.node_error("fetch_context", log_id, e, state)
        # Return fallback state, never raise
        return {...state, "errors": [...state.get("errors", []), str(e)]}
```

### IV. Error Handling Pattern

**Rule:** Nodes NEVER raise exceptions. Always return a fallback state with error appended to state["errors"].

- ✅ **DO:** Log error, append to errors list, return state with fallback data
- ❌ **DON'T:** Raise exceptions from nodes; they break the orchestration graph
- ✅ **DO:** Use AgentLogger.node_error() for observability

**Pattern:**
```python
try:
    result = await some_operation()
    return {...state, "result": result}
except Exception as e:
    log.node_error("node_name", log_id, e, state)
    logger.warning(f"[AGENT] fallback: {e}")
    return {...state, "errors": [...state.get("errors", []), str(e)]}
```

### V. LLM Integration (OLLAMA via LangChain)

**Rule:** LLM calls use LangChain ChatOllama with these settings:

- **Model:** Environment variable `OLLAMA_MODEL` (default: "llama3.2:latest")
- **Base URL:** Environment variable `OLLAMA_BASE_URL` (default: "http://localhost:11434")
- **Parameters:** `temperature=0.2`, `num_predict=600`, `num_ctx=3500`
- **Message format:** Use HumanMessage, SystemMessage from langchain_core.messages

**Pattern:**
```python
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

def get_llm() -> ChatOllama:
    return ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.2,
        num_predict=600,
        num_ctx=3500,
    )

# Usage in nodes:
llm = get_llm()
response = llm.invoke([
    SystemMessage(content=SYSTEM_PROMPT),
    HumanMessage(content=user_input)
])
```

### VI. RAG Context Retrieval (Milvus)

**Rule:** RAG searches query Milvus collection "coaching_scripts" (768-dim embeddings).

- **Collection name:** "coaching_scripts"
- **Embedding dimension:** 768
- **Milvus URI:** "http://localhost:19530" (can be env var)
- **Pattern:** Query → retrieve similar scripts → pass to LLM as context

**Implementation notes:**
- Query building is context-dependent (store-specific, time-aware)
- Retrieved scripts are passed as `rag_context` in state
- Always track `rag_used` boolean and `nb_rag_scripts` count

### VII. Logging & Observability

**Rule:** Use AgentLogger from `agent_logger.py` for all node execution tracking.

- **Constructor:** `AgentLogger(agent_name, cycle_id, store_id)`
- **Methods:**
  - `log.node_start(node_name, state)` → returns `log_id`
  - `log.node_done(node_name, log_id, output, duration_ms, summary_dict)`
  - `log.node_error(node_name, log_id, exception, state)`
- **Standard logging:** Use `logger = logging.getLogger(__name__)` for module-level logs
- **Log format:** `[AGENT_NAME]` prefix for distinguishing agent logs

**Example:**
```python
log = AgentLogger("analyst", cycle_id, store_id)
log_id = log.node_start("compute_gaps", state)
try:
    # ... process ...
    log.node_done("compute_gaps", log_id, result, duration_ms, {"gaps": gaps})
except Exception as e:
    log.node_error("compute_gaps", log_id, e, state)
```

### VIII. Configuration & Environment

**Rule:** Use Pydantic settings with environment variable overrides.

- **Main config:** `from core.config import get_config()`
- **Pattern:** `get_config()` returns a Pydantic model with all settings
- **Environment variables:** Override via `.env` files loaded in order:
  - `inventory-module/.env`
  - `sales-module/.env`
  - Project root `.env` (highest priority, override=True)
- **Critical env vars:**
  - `OLLAMA_BASE_URL`: LLM inference endpoint
  - `OLLAMA_MODEL`: Model name for inference
  - `DATABASE_URL`: PostgreSQL connection string
  - `REDIS_URL`: Redis connection string
  - `MILVUS_URI`: Vector DB endpoint

---

### IX. API Router Organization

**Rule:** FastAPI routers use v1 API prefix and module-specific tags.

- **Pattern:** `APIRouter(prefix="/api/v1/{module}", tags=["{module}"])`
- **Routers initialized in:** `main.py` with `app.include_router(router)`
- **Examples:**
  - Cycle management: `/api/v1/cycle`
  - Forecasting: `/api/v1/forecast`
  - Store data: `/api/v1/stores`
  - Coaching: Custom WebSocket endpoints
- **Status codes:** Return explicit status codes; use HTTPException for errors

### X. Testing Strategy

**Rule:** Use pytest + pytest-asyncio for async test suites.

- **Test location:** `{module}/tests/` directory (e.g., [sales-module/modules/coaching/agents/analyst/tests/test_analyst.py](sales-module/modules/coaching/agents/analyst/tests/test_analyst.py))
- **Async tests:** Mark with `@pytest.mark.asyncio`
- **Test naming:** `test_{function_name}_{scenario}` (e.g., `test_compute_gap_metrics_high`)
- **Fixtures:** Use pytest fixtures for setup (mocks, test data, etc.)
- **Coverage:** Aim for >80% on critical paths (gap calculation, urgency scoring)

---

