import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
from app.inventory.forecasting.sensing_model import SensingModel


def main():
    training_df = pd.read_parquet("training_table.parquet")
    model = SensingModel().train(training_df)
    out_path = "app/inventory/forecasting/models/sensing_model_v1.ubj"
    model.save(out_path)
    print(f"Saved model to {out_path}")


if __name__ == "__main__":
    main()