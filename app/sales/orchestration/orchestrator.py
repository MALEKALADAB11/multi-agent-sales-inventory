


from app.sales.coaching.agents.analyst.agent import get_analyst_agent


analyst = get_analyst_agent()

async def main():
    initial_state = {
        "pos_data": {
            "store_id": "STORE_001",
            "daily_target": 15000.0,
            "closing_hour": 20,
        },
        "pos_history": [],  # sera chargé depuis BigQuery
    }

    result = await analyst.ainvoke(initial_state)

    print(f"Urgence: {result['urgency_level']}")
    print(f"Gap: {result['gap_objectif']:.1f}%")
    print(f"Résumé: {result['analyst_summary']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
