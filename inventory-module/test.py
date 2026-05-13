
import pandas as pd
df = pd.read_csv('inventory-module/data/processed/stock_history.csv')
print('Stores:', df['store_id'].unique())
print('SKUs for I63:', df[df['store_id']=='I63']['sku'].nunique())
print(df[df['store_id']=='I63'].head())
