import pandas as pd
from pathlib import Path
for name in ['sales_history.csv','stock_history.csv']:
    path = Path('data/processed') / name
    it = pd.read_csv(path, engine='c', low_memory=False, on_bad_lines='skip', chunksize=1000, encoding='utf-8')
    chunk = next(it)
    print(name, chunk.shape)
    print(chunk.head(2).to_string(index=False))
