from pathlib import Path
import pandas as pd
for name in ['sales_history.csv','stock_history.csv']:
    path = Path('data/processed') / name
    print(name, path.exists(), path.stat().st_size if path.exists() else None)
    if path.exists():
        print(path.read_text(encoding='utf-8', errors='replace').splitlines()[:5])
        try:
            df = pd.read_csv(path, nrows=5)
            print(df.head().to_string(index=False))
        except Exception as e:
            print(type(e).__name__, e)
