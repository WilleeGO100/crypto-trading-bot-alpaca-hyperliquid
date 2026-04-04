from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class FVGSetup:
    side: str
    zone_top: float
    zone_bottom: float
    gap_size: float
    created_bar_index: int
    setup_age_bars: int
    entry_price: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FVGAnalyzer:
    def __init__(self, min_gap_size: float = 4.0, max_gap_age_bars: int = 60):
        self.min_gap_size = float(min_gap_size)
        self.max_gap_age_bars = int(max_gap_age_bars)
        self.active_zones: List[Dict[str, Any]] = []

    @staticmethod
    def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out.columns = [str(c).strip().lower() for c in out.columns]
        return out

    def _prune_old_zones(self, current_index: int) -> None:
        kept = []
        for zone in self.active_zones:
            age = current_index - int(zone["created_bar_index"])
            if age <= self.max_gap_age_bars:
                zone["age"] = age
                kept.append(zone)
        self.active_zones = kept

    def _register_new_zones(self, df: pd.DataFrame) -> None:
        current_index = len(df) - 1
        if len(df) < 3:
            return

        c1_high = float(df["high"].iloc[-3])
        c1_low = float(df["low"].iloc[-3])
        c3_high = float(df["high"].iloc[-1])
        c3_low = float(df["low"].iloc[-1])

        bull_gap = c3_low - c1_high
        bear_gap = c1_low - c3_high

        if bull_gap >= self.min_gap_size:
            zone = {
                "type": "BUY",
                "top": c3_low,
                "bottom": c1_high,
                "gap_size": bull_gap,
                "created_bar_index": current_index,
            }
            if zone not in self.active_zones:
                self.active_zones.append(zone)

        if bear_gap >= self.min_gap_size:
            zone = {
                "type": "SELL",
                "top": c1_low,
                "bottom": c3_high,
                "gap_size": bear_gap,
                "created_bar_index": current_index,
            }
            if zone not in self.active_zones:
                self.active_zones.append(zone)

    def find_setup(self, df: pd.DataFrame) -> Optional[FVGSetup]:
        if not isinstance(df, pd.DataFrame):
            return None

        df = self._normalize_df(df)

        required = {"high", "low", "close"}
        if not required.issubset(df.columns) or len(df) < 3:
            return None

        current_index = len(df) - 1
        current_price = float(df["close"].iloc[-1])

        self._register_new_zones(df)
        self._prune_old_zones(current_index)

        best_match: Optional[FVGSetup] = None

        for zone in list(self.active_zones):
            if zone["bottom"] <= current_price <= zone["top"]:
                age = current_index - int(zone["created_bar_index"])
                setup = FVGSetup(
                    side=zone["type"],
                    zone_top=float(zone["top"]),
                    zone_bottom=float(zone["bottom"]),
                    gap_size=float(zone["gap_size"]),
                    created_bar_index=int(zone["created_bar_index"]),
                    setup_age_bars=int(age),
                    entry_price=current_price,
                    reason=f"{zone['type']} retest into FVG",
                )

                if best_match is None or setup.gap_size > best_match.gap_size:
                    best_match = setup

        if best_match:
            self.active_zones = [
                z
                for z in self.active_zones
                if not (
                    z["type"] == best_match.side
                    and float(z["top"]) == float(best_match.zone_top)
                    and float(z["bottom"]) == float(best_match.zone_bottom)
                )
            ]

        return best_match