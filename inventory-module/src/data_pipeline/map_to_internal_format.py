"""
Data Mapping Script - Convert Real Data to Your Internal Format
Maps cleaned real data to match your existing CSV structure
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# ── Robust path resolution ────────────────────────────────────────────────────
# Script lives at: inventory-module/src/data_pipeline/map_to_internal_format.py
# Project root is 4 levels up from this file
SCRIPT_DIR   = Path(__file__).resolve().parent                  # .../data_pipeline/
PROJECT_ROOT = SCRIPT_DIR.parents[2]                            # multi-agent-sales-inventory/

CLEANED_PATH = PROJECT_ROOT / "inventory-module" / "data" / "cleaned"
OUTPUT_PATH  = PROJECT_ROOT / "inventory-module" / "data" / "processed"
# ─────────────────────────────────────────────────────────────────────────────


def map_product_master(product_clean_df, boutique_df=None):
    """Map product_cleaned.csv → product_master.csv"""
    print("🔄 Mapping product data to internal format...")

    product_master = pd.DataFrame({
        'sku':              product_clean_df['COD_PROD'],
        'product_name':     product_clean_df['DES_PROD'],
        'category':         product_clean_df['COD_GROUP'].fillna('TELECOM'),
        'unit_cost':        product_clean_df['PA_HT'],
        'unit_price':       product_clean_df['PV_HT'],
        'lead_time_days':   product_clean_df['lead_time_days'],
        'lead_time_std':    product_clean_df['lead_time_std'],
        'moq':              product_clean_df['moq'],
        'holding_cost_pct': product_clean_df['holding_cost_pct'],
        'order_cost':       product_clean_df['order_cost'],
        'lifecycle_stage':  product_clean_df['lifecycle_stage'],
        # service_level_target handled by DB/settings, not CSV
    })

    print(f"✅ Mapped {len(product_master)} products")
    return product_master


def map_sales_history(transactions_expanded_df, boutique_df):
    """Map transactions_expanded.csv → sales_history.csv"""
    print("🔄 Mapping sales history to internal format...")

    store_lookup = (
        boutique_df.set_index('CD_DIST')['NOM_DIST'].to_dict()
        if boutique_df is not None else {}
    )

    region_mapping = {
        'TUNIS':   'North',
        'SFAX':    'Central',
        'SOUSSE':  'Central',
        'GABES':   'South',
        'BIZERTE': 'North',
        'ARIANA':  'North',
    }

    ville_to_region = (
        boutique_df.set_index('CD_DIST')['VILLE']
        .map(lambda v: region_mapping.get(str(v).upper(), 'Central'))
        .to_dict()
        if boutique_df is not None else {}
    )

    sales_history = pd.DataFrame({
        'date':          pd.to_datetime(transactions_expanded_df['DATE_VENTE']).dt.date,
        'store_id':      transactions_expanded_df['CODE_CENTRE'],
        'store_name':    transactions_expanded_df['CODE_CENTRE'].map(store_lookup).fillna('Unknown Store'),
        'region':        transactions_expanded_df['CODE_CENTRE'].map(ville_to_region).fillna('Central'),
        'sku':           transactions_expanded_df['CODE_PRODUIT'],
        'product_name':  transactions_expanded_df['DES_PRODUIT'],
        'category':      transactions_expanded_df['CATEGORIE_PRODUIT'].fillna('TELECOM'),
        'quantity_sold': transactions_expanded_df['QTE_PRODUIT'],
        'revenue':       transactions_expanded_df['LIG_TTC'],
        'unit_price':    transactions_expanded_df['LIG_TTC'] / transactions_expanded_df['QTE_PRODUIT'].replace(0, 1),
        'is_promo':      (transactions_expanded_df['TX_REM'] > 0).astype(int),
        'event_name':    '',
        'event_type':    '',
        'season':        '',
    })

    def get_season(d):
        month = pd.to_datetime(d).month
        if month in [12, 1, 2]: return 'Winter'
        if month in [3, 4, 5]:  return 'Spring'
        if month in [6, 7, 8]:  return 'Summer'
        return 'Fall'

    def get_event(d):
        dt = pd.to_datetime(d)
        month, day = dt.month, dt.day
        if month == 9  and 1  <= day <= 15: return 'Back_to_School', 'shopping'
        if month == 12 and 15 <= day <= 31: return 'Year_End',       'holiday'
        if month == 7:                      return 'Summer_Sale',    'shopping'
        if month in [3, 4]:                 return 'Ramadan',        'religious'
        return '', ''

    sales_history['season'] = sales_history['date'].apply(get_season)
    sales_history[['event_name', 'event_type']] = sales_history['date'].apply(
        lambda d: pd.Series(get_event(d))
    )

    print(f"✅ Mapped {len(sales_history)} sales records")
    return sales_history


def map_stock_history(stock_history_generated_df, boutique_df):
    """Map stock_history_generated.csv → stock_history.csv"""
    print("🔄 Mapping stock history to internal format...")

    store_lookup = (
        boutique_df.set_index('CD_DIST')['NOM_DIST'].to_dict()
        if boutique_df is not None else {}
    )

    stock_history = pd.DataFrame({
        'date':         pd.to_datetime(stock_history_generated_df['date']).dt.date,
        'store_id':     stock_history_generated_df['store_id'],
        'store_name':   stock_history_generated_df['store_id'].map(store_lookup).fillna('Unknown Store'),
        'region':       'Central',   # extend with region lookup if needed
        'sku':          stock_history_generated_df['sku'],
        'product_name': '',          # join from product_master if needed
        'category':     '',
        'stock_level':  stock_history_generated_df['stock_level'],
        'is_stockout':  stock_history_generated_df['is_stockout'],
    })

    print(f"✅ Mapped {len(stock_history)} stock records")
    return stock_history


def map_promotions(promotions_extracted_df):
    """Map promotions_extracted.csv → promotions.csv"""
    print("🔄 Mapping promotions to internal format...")

    promotions = pd.DataFrame({
        'promo_id':    promotions_extracted_df['promo_id'],
        'promo_name':  promotions_extracted_df['promo_name'],
        'start_date':  pd.to_datetime(promotions_extracted_df['start_date']).dt.date,
        'end_date':    pd.to_datetime(promotions_extracted_df['end_date']).dt.date,
        'sku':         promotions_extracted_df['sku'],
        'product_name': '',
        'category':    '',
        'discount_pct': promotions_extracted_df['discount_pct'],
        'promo_type':  promotions_extracted_df['promo_type'],
        'scope':       promotions_extracted_df['scope'],
    })

    print(f"✅ Mapped {len(promotions)} promotions")
    return promotions


def main():
    print("\n" + "="*60)
    print("🔄 DATA MAPPING PIPELINE - Real → Internal Format")
    print("="*60 + "\n")

    print(f"📁 Input  : {CLEANED_PATH}")
    print(f"📁 Output : {OUTPUT_PATH}\n")

    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Load cleaned data
        print("📂 Loading cleaned data files...\n")
        product_clean         = pd.read_csv(CLEANED_PATH / "product_cleaned.csv")
        transactions_expanded = pd.read_csv(CLEANED_PATH / "transactions_expanded.csv")
        stock_history_gen     = pd.read_csv(CLEANED_PATH / "stock_history_generated.csv")
        promotions_extracted  = pd.read_csv(CLEANED_PATH / "promotions_extracted.csv")
        boutique              = pd.read_csv(CLEANED_PATH / "boutique_cleaned.csv")

        # 2. Map to internal format
        product_master = map_product_master(product_clean, boutique)
        sales_history  = map_sales_history(transactions_expanded, boutique)
        stock_history  = map_stock_history(stock_history_gen, boutique)
        promotions     = map_promotions(promotions_extracted)

        # 3. Save
        print("\n💾 Saving mapped data to internal format...")
        product_master.to_csv(OUTPUT_PATH / "product_master.csv", index=False)
        sales_history.to_csv(OUTPUT_PATH / "sales_history.csv",   index=False)
        stock_history.to_csv(OUTPUT_PATH / "stock_history.csv",   index=False)
        promotions.to_csv(OUTPUT_PATH / "promotions.csv",         index=False)

        print("\n" + "="*60)
        print("✅ DATA MAPPING COMPLETE!")
        print("="*60)
        print(f"\n📁 Mapped files saved to: {OUTPUT_PATH}")
        print("\n📊 Summary:")
        print(f"   • product_master.csv : {len(product_master)} products")
        print(f"   • sales_history.csv  : {len(sales_history)} records")
        print(f"   • stock_history.csv  : {len(stock_history)} records")
        print(f"   • promotions.csv     : {len(promotions)} promotions")
        print("\n🎉 Ready! Update settings.py to point to data/processed/")

    except FileNotFoundError as e:
        print(f"\n❌ File not found: {e}")
        print(f"   Make sure you ran clean_real_data.py first.")
        print(f"   Expected cleaned files in: {CLEANED_PATH}")
        import traceback; traceback.print_exc()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()
