#!/usr/bin/env python3
"""
test_ollama_timeout_fix.py — Verify RAG embedding timeout fix

Run with:
    python test_ollama_timeout_fix.py
"""

import asyncio
import sys
import requests
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
TIMEOUT = 120  # ← NEW timeout value


async def test_embedding():
    """Test RAG embedding with new timeout."""
    logger.info("🧪 Testing OLLAMA embedding with 120s timeout...")
    
    test_queries = [
        "gap critique urgent closing bundle terminal",
        "performance correcte optimiser panier moyen services",
        "pluie météo défavorable accessoires résistants eau",
    ]
    
    results = []
    
    for i, query in enumerate(test_queries, 1):
        logger.info(f"Test {i}/3: '{query[:50]}...'")
        
        try:
            start = datetime.now()
            
            resp = requests.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": query},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            embedding = resp.json().get("embedding", [])
            
            duration = (datetime.now() - start).total_seconds()
            
            if embedding:
                logger.info(f"  ✅ Success in {duration:.1f}s — {len(embedding)} dims")
                results.append(("success", duration))
            else:
                logger.warning(f"  ⚠️  Empty embedding after {duration:.1f}s")
                results.append(("empty", duration))
                
        except requests.Timeout:
            logger.error(f"  ❌ TIMEOUT after {TIMEOUT}s — OLLAMA too slow")
            results.append(("timeout", TIMEOUT))
            
        except requests.ConnectionError as e:
            logger.error(f"  ❌ CONNECTION ERROR: {e}")
            results.append(("connection_error", 0))
            
        except Exception as e:
            logger.error(f"  ❌ ERROR: {str(e)[:80]}")
            results.append(("error", 0))
    
    # ── Summary ────────────────────────────────────────────────────────────
    logger.info("\n" + "="*60)
    logger.info("SUMMARY")
    logger.info("="*60)
    
    successes = [r for r in results if r[0] == "success"]
    failures = [r for r in results if r[0] != "success"]
    
    if successes:
        avg_time = sum(r[1] for r in successes) / len(successes)
        logger.info(f"✅ Successes: {len(successes)}/3")
        logger.info(f"⏱️  Average time: {avg_time:.1f}s")
    
    if failures:
        logger.info(f"❌ Failures: {len(failures)}/3")
        for ftype, duration in failures:
            logger.info(f"   - {ftype}: {duration}s")
    
    if len(successes) == 3:
        logger.info("\n🎉 ALL TESTS PASSED — 120s timeout is sufficient")
        return 0
    else:
        logger.warning(f"\n⚠️  {len(failures)} tests failed — may need further investigation")
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(test_embedding())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.warning("\n⏸️  Test interrupted by user")
        sys.exit(1)
