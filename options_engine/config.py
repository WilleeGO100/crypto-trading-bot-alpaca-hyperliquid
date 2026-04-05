from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


@dataclass
class PMCPConfig:
    # Strategy
    underlying_symbol: str = "SPY"
    strategy_name: str = "poor_man_covered_put"
    long_put_dte_min: int = 120
    long_put_dte_max: int = 540
    short_put_dte_min: int = 21
    short_put_dte_max: int = 45
    target_long_delta_abs: float = 0.80
    target_short_delta_abs: float = 0.30
    min_open_interest: int = 50
    max_net_debit_per_spread: float = 30.0
    contracts_qty: int = 1

    # Broker/data
    broker: str = "alpaca"
    alpaca_paper_trade: bool = True
    options_feed: str = "indicative"  # opra or indicative
    min_options_level: int = 3

    # Execution control
    dry_run: bool = True
    submit_orders: bool = False

    # Paths
    data_dir: str = "data/options"
    state_file: str = "data/options/pmcp_state.json"
    plans_csv: str = "data/options/pmcp_plans.csv"
    last_plan_json: str = "data/options/pmcp_last_plan.json"

    @classmethod
    def from_sources(cls, config_path: Path) -> "PMCPConfig":
        file_payload: Dict[str, Any] = {}
        if config_path.exists():
            try:
                file_payload = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                file_payload = {}

        defaults = cls()
        merged = asdict(defaults)
        merged.update(file_payload)

        merged["underlying_symbol"] = os.getenv(
            "OPTIONS_UNDERLYING", merged["underlying_symbol"]
        ).strip().upper()
        merged["alpaca_paper_trade"] = _env_bool(
            "ALPACA_PAPER_TRADE", bool(merged["alpaca_paper_trade"])
        )
        merged["dry_run"] = _env_bool("OPTIONS_DRY_RUN", bool(merged["dry_run"]))
        merged["submit_orders"] = _env_bool(
            "OPTIONS_SUBMIT_ORDERS", bool(merged["submit_orders"])
        )
        merged["options_feed"] = os.getenv(
            "OPTIONS_FEED", str(merged["options_feed"])
        ).strip().lower()

        merged["long_put_dte_min"] = _env_int(
            "PMCP_LONG_DTE_MIN", int(merged["long_put_dte_min"])
        )
        merged["long_put_dte_max"] = _env_int(
            "PMCP_LONG_DTE_MAX", int(merged["long_put_dte_max"])
        )
        merged["short_put_dte_min"] = _env_int(
            "PMCP_SHORT_DTE_MIN", int(merged["short_put_dte_min"])
        )
        merged["short_put_dte_max"] = _env_int(
            "PMCP_SHORT_DTE_MAX", int(merged["short_put_dte_max"])
        )
        merged["target_long_delta_abs"] = _env_float(
            "PMCP_TARGET_LONG_DELTA_ABS", float(merged["target_long_delta_abs"])
        )
        merged["target_short_delta_abs"] = _env_float(
            "PMCP_TARGET_SHORT_DELTA_ABS", float(merged["target_short_delta_abs"])
        )
        merged["min_open_interest"] = _env_int(
            "PMCP_MIN_OPEN_INTEREST", int(merged["min_open_interest"])
        )
        merged["max_net_debit_per_spread"] = _env_float(
            "PMCP_MAX_NET_DEBIT", float(merged["max_net_debit_per_spread"])
        )
        merged["contracts_qty"] = _env_int(
            "PMCP_CONTRACTS_QTY", int(merged["contracts_qty"])
        )
        merged["min_options_level"] = _env_int(
            "PMCP_MIN_OPTIONS_LEVEL", int(merged["min_options_level"])
        )

        return cls(**merged)

