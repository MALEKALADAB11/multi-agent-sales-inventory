"""
Data Cleaning Script - Fill Missing Values in Real Data
Fills empty fields with smart defaults based on available data
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

# ── Robust path resolution ────────────────────────────────────────────────────
# Script lives at: inventory-module/src/data_pipeline/clean_real_data.py
# Project root is 4 levels up from this file
SCRIPT_DIR   = Path(__file__).resolve().parent                  # .../data_pipeline/
PROJECT_ROOT = SCRIPT_DIR.parents[2]                            # multi-agent-sales-inventory/

INPUT_PATH  = PROJECT_ROOT / "shared_module" / "data" / "raw"
OUTPUT_PATH = PROJECT_ROOT / "inventory-module" / "data" / "cleaned"
# ─────────────────────────────────────────────────────────────────────────────


def clean_product_data(product_df):
    """Clean product.xls - fill missing cost, lead times, MOQ, etc."""
    print(" Cleaning product data...")

    # Remove products with PV_HT = 0 (likely data errors)
    original_count = len(product_df)
    product_df = product_df[product_df['PV_HT'] > 0].copy()
    if len(product_df) < original_count:
        print(f"   ⚠️  Removed {original_count - len(product_df)} products with PV_HT = 0")

    # Fill missing unit costs (PA_HT) with smart logic:
    # 1. If exists and > 0, keep it
    # 2. Else, use average PA_HT from same family (COD_FAM)
    # 3. Else, use average PA_HT from same group (COD_GROUP)
    # 4. Else, use 60% of selling price
    family_avg_cost = product_df[product_df['PA_HT'] > 0].groupby('COD_FAM')['PA_HT'].mean()
    group_avg_cost  = product_df[product_df['PA_HT'] > 0].groupby('COD_GROUP')['PA_HT'].mean()

    def fill_cost(row):
        if pd.notna(row['PA_HT']) and row['PA_HT'] > 0:
            return row['PA_HT']
        if pd.notna(row['COD_FAM']) and row['COD_FAM'] in family_avg_cost:
            return family_avg_cost[row['COD_FAM']]
        if pd.notna(row['COD_GROUP']) and row['COD_GROUP'] in group_avg_cost:
            return group_avg_cost[row['COD_GROUP']]
        return row['PV_HT'] * 0.6 if pd.notna(row['PV_HT']) and row['PV_HT'] > 0 else 0

    product_df['PA_HT'] = product_df.apply(fill_cost, axis=1)

    # Lead times
    product_df['lead_time_days'] = 10
    product_df['lead_time_std']  = 3

    # MOQ based on price
    product_df['moq'] = product_df['PV_HT'].apply(
        lambda p: 100 if p < 50 else 50 if p < 200 else 20
    )

    # Fixed costs
    product_df['order_cost']       = 50    # 50 TND per order
    product_df['holding_cost_pct'] = 0.20  # 20% per year

    # Lifecycle stage
    product_df['lifecycle_stage'] = product_df.apply(
        lambda row: 'decline' if row.get('EOL', 'N') == 'Y'
        else 'mature' if row.get('ACTIF', 'Y') == 'Y'
        else 'growth',
        axis=1
    )

    # Fill QTE_MIN / QTE_MAX
    product_df['QTE_MIN'] = product_df['QTE_MIN'].fillna(10)
    product_df['QTE_MAX'] = product_df['QTE_MAX'].fillna(100)

    print(f"✅ Cleaned {len(product_df)} products")
    return product_df


def expand_sales_history(transaction_df, months=18):
    """Expand 1 month of transactions to 18 months using seasonal patterns"""
    print(f"📈 Expanding sales from 1 month to {months} months...")

    transaction_df['DATE_VENTE'] = pd.to_datetime(transaction_df['DATE_VENTE'])
    real_month = transaction_df['DATE_VENTE'].dt.to_period('M').iloc[0]

    seasonal_mult = {
        1: 0.80,  # January  - post-holiday low
        2: 0.85,  # February
        3: 1.00,  # March    - baseline (real data)
        4: 0.90,  # April
        5: 0.95,  # May
        6: 1.10,  # June     - end of school
        7: 1.20,  # July     - summer peak
        8: 1.15,  # August
        9: 1.30,  # September- back to school PEAK
        10: 1.00, # October
        11: 1.10, # November
        12: 1.40, # December - holidays PEAK
    }

    expanded_data = []
    for month_offset in range(-months + 1, 1):
        target_date = real_month.to_timestamp() + pd.DateOffset(months=month_offset)
        multiplier  = seasonal_mult[target_date.month]

        month_data = transaction_df.copy()
        month_data['DATE_VENTE']   = target_date
        month_data['QTE_PRODUIT']  = (
            month_data['QTE_PRODUIT'] * multiplier *
            np.random.uniform(0.9, 1.1, len(month_data))
        ).astype(int)
        month_data['LIG_TTC'] = (
            month_data['QTE_PRODUIT'] *
            month_data['LIG_TTC'] / transaction_df['QTE_PRODUIT']
        )
        expanded_data.append(month_data)

    result = pd.concat(expanded_data, ignore_index=True)
    print(f"✅ Expanded to {len(result)} rows ({months} months)")
    return result


def generate_stock_history(stock_centre_df, transaction_df, months=12):
    """Generate historical stock levels from current snapshot"""
    print(f"📦 Generating {months} months of stock history...")

    stock_history = []
    current_date  = pd.to_datetime('2026-01-22')

    for month_offset in range(-months, 1):
        date = current_date + pd.DateOffset(months=month_offset)
        for _, row in stock_centre_df.iterrows():
            base_stock = row['QTE_STK']
            noise = np.random.normal(0, max(base_stock * 0.1, 0))
            stock_history.append({
                'date':        date,
                'store_id':    row['CD_DIST'],
                'sku':         row['COD_PROD'],
                'stock_level': max(0, int(base_stock + noise)),
                'is_stockout': 0,
            })

    result = pd.DataFrame(stock_history)
    print(f"✅ Generated {len(result)} stock records")
    return result


def generate_promotions_from_transactions(transaction_df):
    """Extract promotions from transaction data"""
    print("🎁 Extracting promotions from transactions...")

    promo_txns = transaction_df[
        (transaction_df['TX_REM'] > 0) |
        (transaction_df['CODE_ACTION'].notna())
    ].copy()

    if len(promo_txns) == 0:
        print("⚠️  No promotions found, creating empty file")
        return pd.DataFrame(columns=[
            'promo_id', 'promo_name', 'start_date', 'end_date',
            'sku', 'discount_pct', 'promo_type', 'scope'
        ])

    promotions = []
    for (code_prod, code_action), group in promo_txns.groupby(['CODE_PRODUIT', 'CODE_ACTION']):
        promotions.append({
            'promo_id':    f"PROMO_{len(promotions)+1:04d}",
            'promo_name':  f"Discount_{code_action}" if pd.notna(code_action) else "General_Discount",
            'start_date':  group['DATE_VENTE'].min(),
            'end_date':    group['DATE_VENTE'].max(),
            'sku':         code_prod,
            'discount_pct': group['TX_REM'].mean(),
            'promo_type':  'discount',
            'scope':       'product',
        })

    result = pd.DataFrame(promotions)
    print(f"✅ Found {len(result)} promotions")
    return result


def main():
    print("\n" + "="*60)
    print("🚀 DATA CLEANING PIPELINE - Real Data Preparation")
    print("="*60 + "\n")

    print(f"📁 Input  : {INPUT_PATH}")
    print(f"📁 Output : {OUTPUT_PATH}\n")

    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Load raw data
        print("📂 Loading raw data files...\n")
        product_df     = pd.read_excel(INPUT_PATH / "product.xls")
        transaction_df = pd.read_csv(INPUT_PATH / "transaction_vente.csv")
        stock_df       = pd.read_excel(INPUT_PATH / "stock_centre.xls")
        boutique_df    = pd.read_excel(INPUT_PATH / "boutique_actif.xls")

        # 2. Clean product data
        product_clean = clean_product_data(product_df)

        # 3. Expand sales history
        sales_expanded = expand_sales_history(transaction_df, months=18)

        # 4. Generate stock history
        stock_history = generate_stock_history(stock_df, transaction_df, months=12)

        # 5. Extract promotions
        promotions = generate_promotions_from_transactions(transaction_df)

        # 6. Save
        print("\n💾 Saving cleaned data...")
        product_clean.to_csv(OUTPUT_PATH / "product_cleaned.csv",            index=False)
        sales_expanded.to_csv(OUTPUT_PATH / "transactions_expanded.csv",     index=False)
        stock_history.to_csv(OUTPUT_PATH / "stock_history_generated.csv",    index=False)
        promotions.to_csv(OUTPUT_PATH / "promotions_extracted.csv",          index=False)
        boutique_df.to_csv(OUTPUT_PATH / "boutique_cleaned.csv",             index=False)

        print("\n" + "="*60)
        print("✅ DATA CLEANING COMPLETE!")
        print("="*60)
        print(f"\n📁 Cleaned files saved to: {OUTPUT_PATH}")
        print("\nNext step: run map_to_internal_format.py")

    except FileNotFoundError as e:
        print(f"\n❌ File not found: {e}")
        print(f"   Make sure your raw files exist under: {INPUT_PATH}")
        import traceback; traceback.print_exc()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()
