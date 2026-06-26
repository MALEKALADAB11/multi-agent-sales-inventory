# test_tools.py
import asyncio
from modules.coaching.agents.stratege.tools import fetch_full_context

async def main():
    ctx = await fetch_full_context("OOR_LAC_01")
    print(f"Météo    : {ctx['summary']['weather_icon']} {ctx['summary']['weather_label']}")
    print(f"Effet    : {ctx['summary']['weather_effect']:+.0%}")
    print(f"Férié    : {ctx['summary']['is_holiday']}")
    print(f"Promos   : {ctx['summary']['active_promos']}")
    print(f"Heatmap  : {ctx['heatmap']}")

asyncio.run(main())
