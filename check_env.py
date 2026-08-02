import os
from dotenv import load_dotenv

load_dotenv()

print("CRITICAL_TREND variables:")
print(f"  CRITICAL_TREND_INTERVAL_MINUTES: {os.getenv('CRITICAL_TREND_INTERVAL_MINUTES', 'not set')}")
print(f"  CRITICAL_TREND_FORCE_ACTIVITY: {os.getenv('CRITICAL_TREND_FORCE_ACTIVITY', 'not set')}")
print(f"  CRITICAL_TREND_REPLAY_DAYS_BACK: {os.getenv('CRITICAL_TREND_REPLAY_DAYS_BACK', 'not set')}")
print(f"  CRITICAL_TREND_REPLAY_SPEED: {os.getenv('CRITICAL_TREND_REPLAY_SPEED', 'not set')}")
