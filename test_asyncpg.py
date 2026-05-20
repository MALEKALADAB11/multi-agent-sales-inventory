# test_asyncpg.py
import asyncio
import asyncpg

async def test():
    try:
        conn = await asyncpg.connect(
            host="localhost",
            port=5433,
            user="asc_user",
            password="asc_password",
            database="asc_db",
            timeout=5
        )
        result = await conn.fetch("SELECT COUNT(*) FROM inv.products")
        print(f"✅ Asyncpg works! Products: {result[0]['count']}")
        await conn.close()
    except Exception as e:
        print(f"❌ Asyncpg failed: {e}")

asyncio.run(test())