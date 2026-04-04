from pathlib import Path

import pandas as pd


class SignalGenerator:
    def __init__(self, csv_path: Path):
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.csv_path.exists():
            pd.DataFrame(
                columns=[
                    "DateTime",
                    "Direction",
                    "Entry_Price",
                    "Stop_Loss",
                    "Take_Profit",
                ]
            ).to_csv(self.csv_path, index=False)

    def append_signal(
        self,
        timestamp: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> None:
        row = pd.DataFrame(
            [
                {
                    "DateTime": timestamp,
                    "Direction": direction,
                    "Entry_Price": round(entry_price, 2),
                    "Stop_Loss": round(stop_loss, 2),
                    "Take_Profit": round(take_profit, 2),
                }
            ]
        )
        row.to_csv(self.csv_path, mode="a", header=False, index=False)