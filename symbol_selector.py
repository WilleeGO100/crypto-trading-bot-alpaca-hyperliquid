"""
Selector module that chooses an active trade symbol from scanner output.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from env_profiles import load_env_profile
from market_scanner import RANKINGS_PATH, run_scan

BASE_DIR = Path(__file__).resolve().parent
LOADED_ENV_PROFILE = load_env_profile("engine")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SELECTOR_STATE_PATH = DATA_DIR / "symbol_selector_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_rankings() -> dict:
    if RANKINGS_PATH.exists():
        try:
            return json.loads(RANKINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    top_n = int(os.getenv("SCANNER_TOP_N", "5"))
    return run_scan(top_n=top_n)


def _persist_selection(selection: Dict[str, str]) -> None:
    payload = {"timestamp": _now_iso(), "selection": selection}
    SELECTOR_STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def choose_symbol_for_broker(broker: str) -> Dict[str, str]:
    rankings = _load_rankings().get("rankings", [])
    if not rankings:
        selection = {
            "coin": "BTC",
            "market_symbol": "BTC-USD",
            "trade_symbol": "BTC/USD" if broker == "alpaca" else "BTC",
            "broker": broker,
            "reason": "fallback_no_rankings",
        }
        _persist_selection(selection)
        return selection

    best = rankings[0]
    trade_symbol = (
        best.get("trade_symbol_alpaca", "BTC/USD")
        if broker == "alpaca"
        else best.get("trade_symbol_hyperliquid", "BTC")
    )
    selection = {
        "coin": best.get("coin", "BTC"),
        "market_symbol": best.get("market_symbol", "BTC-USD"),
        "trade_symbol": trade_symbol,
        "broker": broker,
        "reason": "top_ranked_by_scanner",
    }
    _persist_selection(selection)
    return selection


if __name__ == "__main__":
    broker = os.getenv("BROKER", "hyperliquid").strip().lower()
    print(f"[CONFIG] Loaded env profile: {LOADED_ENV_PROFILE}")
    selected = choose_symbol_for_broker(broker=broker)
    print(f"[SELECTOR] broker={broker} coin={selected['coin']} trade_symbol={selected['trade_symbol']}")
