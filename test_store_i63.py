import pandas as pd

def read_file(file_path):
    """Automatically read CSV or Excel files based on extension"""
    if file_path.endswith('.csv'):
        return pd.read_csv(file_path, low_memory=False)
    elif file_path.endswith(('.xls', '.xlsx')):
        return pd.read_excel(file_path)
    else:
        raise ValueError(f"Unsupported file format: {file_path}")

print("="*60)
print("SKU COUNT FOR STORE 'i63'")
print("="*60)

# RAW DATA
print("\n📁 RAW DATA:")
print("-"*40)

# 1. Transaction file
try:
    df = read_file('shared_module\\data\\raw\\transaction_vente.csv')
    store_df = df[df['CODE_CENTRE'].astype(str).str.lower() == 'i63']
    skus = store_df['CODE_PRODUIT'].nunique()
    print(f"Transaction file: {skus} SKUs")
except Exception as e:
    print(f"Transaction file: Error - {e}")

# 2. Stock centre file
try:
    df = read_file('shared_module\\data\\raw\\stock_centre.xls')
    store_df = df[df['CD_DIST'].astype(str).str.lower() == 'i63']
    skus = store_df['COD_PROD'].nunique()
    print(f"Stock centre file: {skus} SKUs")
except Exception as e:
    print(f"Stock centre file: Error - {e}")

# 3. Boutique actif file (Excel)
try:
    df = read_file('shared_module\\data\\raw\\boutique_actif.xls')
    if 'CD_DIST' in df.columns:
        exists = (df['CD_DIST'].astype(str).str.lower() == 'i63').any()
        print(f"Boutique actif file: Store i63 {'found' if exists else 'not found'}")
    else:
        print(f"Boutique actif file: Column 'CD_DIST' not found")
except Exception as e:
    print(f"Boutique actif file: Error - {e}")

# 4. Product file (Excel)
try:
    df = read_file('shared_module\\data\\raw\\product.xls')
    skus = df['COD_PROD'].nunique()
    print(f"Product file: {skus} SKUs")
except Exception as e:
    print(f"Product file: Error - {e}")

# PROCESSED DATA
print("\n📁 PROCESSED DATA:")
print("-"*40)

# 5. Sales history
try:
    df = read_file('inventory-module\\data\\processed\\sales_history.csv')
    store_df = df[df['store_id'].astype(str).str.lower() == 'i63']
    skus = store_df['sku'].nunique()
    print(f"Sales history: {skus} SKUs")
except Exception as e:
    print(f"Sales history: Error - {e}")

# 6. Stock history
try:
    df = read_file('inventory-module\\data\\processed\\stock_history.csv')
    store_df = df[df['store_id'].astype(str).str.lower() == 'i63']
    skus = store_df['sku'].nunique()
    print(f"Stock history: {skus} SKUs")
except Exception as e:
    print(f"Stock history: Error - {e}")

# 7. Product master
try:
    df = read_file('inventory-module\\data\\processed\\product_master.csv')
    skus = df['sku'].nunique()
    print(f"Product master: {skus} SKUs")
except Exception as e:
    print(f"Product master: Error - {e}")

# 8. Promotions
try:
    df = read_file('inventory-module\\data\\processed\\promotions.csv')
    if 'sku' in df.columns:
        skus = df['sku'].nunique()
        print(f"Promotions: {skus} SKUs")
    else:
        print(f"Promotions: No SKU column")
except Exception as e:
    print(f"Promotions: Error - {e}")

# 9. Forecast
try:
    df = read_file('inventory-module\\data\\forecasts\\timesFM_future_forecast.csv')
    if 'store_id' in df.columns and 'sku' in df.columns:
        store_df = df[df['store_id'].astype(str).str.lower() == 'i63']
        skus = store_df['sku'].nunique()
        print(f"Forecast: {skus} SKUs")
    else:
        print(f"Forecast: Required columns not found")
except Exception as e:
    print(f"Forecast: Error - {e}")

print("\n" + "="*60)