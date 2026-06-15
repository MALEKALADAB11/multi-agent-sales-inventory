# OLLAMA Timeout Fix — Summary

## Problem
RAG embedding calls were timing out at 30 seconds:
```
[STRATEGE RAG] Erreur: HTTPConnectionPool(host='localhost', port=11434): Read timed out. (read timeout=
[AGENT_LOGGER] ❌ [stratege] rag_search — timeout: HTTPConnectionPool(host='localhost', port=11434): Read timed
```

## Root Cause
- OLLAMA can be slow under load when generating embeddings for RAG queries
- Default 30-second timeout was too aggressive
- When backend and OLLAMA are on the same machine, memory/CPU contention can cause delays

## Solution Implemented

### 1. **data/rag_retriever.py** — `_embed()` function
- **Changed**: `timeout=30` → `timeout=120` (4x increase)
- **Added**: Specific error handling for `requests.Timeout` and `requests.ConnectionError`
- **Behavior**: If embedding fails, returns None → RAG becomes unavailable but system doesn't crash

### 2. **modules/coaching/agents/stratege/nodes.py** — RAG search node
- **Changed**: `timeout=30` → `timeout=120` 
- **Added**: Nested try-catch for embedding with detailed error logging
- **Behavior**: Logs warnings but continues with empty embedding if OLLAMA fails

## Timeouts Configuration

| Component | Timeout | Rationale |
|-----------|---------|-----------|
| RAG Embedding | 120s | OLLAMA inference can take 30-90s under load |
| Coach Strategist Orchestrator | 30s | Managed separately, has retry/fallback logic |
| Database Queries | 5-10s | AsyncPg connections, fast operations |

## Testing the Fix

### Quick Verification
```bash
# Test OLLAMA is responsive
curl -s http://localhost:11434/api/tags | jq '.models | length'
# Should show: 5 (or your number of models)
```

### Functional Test
1. Send a message via Frontend to Coach Chat
2. Watch logs for:
   ```
   [RAG] Embedding succès (87 chars)      ✅ Success
   [STRATEGE RAG] Embedding timeout (120s) ❌ Still slow after 2 min
   [RAG] Embedding connexion échouée      ❌ Connection issue
   ```

### Performance Monitoring
```bash
# Monitor OLLAMA CPU usage
docker stats --no-stream | grep ollama
# Watch for high CPU% (>80%) indicating slow inference
```

## Fallback Behavior

If embedding fails:
1. **RAG becomes unavailable** (`rag_used=false`)
2. **Strategist still generates actions** using context clues
3. **Coach still responds** with LLM output minus RAG scripts
4. **Log entry** records the failure for debugging

## Deployment Notes

- ✅ No database changes required
- ✅ No environment variables needed
- ✅ Change is backward compatible
- ✅ Can be reverted by changing timeout back to 30
- ⚠️  If timeouts still occur after 120s, consider:
  - Upgrading OLLAMA model (current: llama3.2:3.2B)
  - Running OLLAMA on separate machine
  - Caching embeddings in Redis

## Related Configuration Files

- `OLLAMA_BASE_URL` = "http://localhost:11434" (env var, default)
- `OLLAMA_MODEL` = "llama3.2:latest" (env var, default)
- Embedding model = "nomic-embed-text" (768 dims, 137M params)

## Metrics to Track

Add to monitoring dashboard:
- `rag_embedding_latency_ms` (avg, p95, p99)
- `rag_embedding_timeout_count` (daily)
- `rag_availability_pct` (should be >99%)
