"""
Market scanner that ranks symbols by tradability and outputs top-N candidates.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
from dotenv import load_dotenv
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RANKINGS_PATH = DATA_DIR / "scanner_rankings.json"

DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "ADA"]
IS_TESTNET = os.getenv("USE_TESTNET", "True").strip().lower() == "true"
BASE_URL = TESTNET_API_URL if IS_TESTNET else MAINNET_API_URL
_INFO_CLIENT: Optional[Info] = None


@dataclass
class ScanResult:
    coin: str
    market_symbol: str
    score: float
    volatility: float
    dollar_volume: float
    trend_strength: float
    range_pct: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_universe() -> List[str]:
    raw = os.getenv("SCANNER_UNIVERSE", "")
    if not raw.strip():
        return DEFAULT_UNIVERSE
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        v = float(value)
        if pd.isna(v):
            return default
        return v
    except Exception:
        return default


def _get_info_client() -> Optional[Info]:
    global _INFO_CLIENT
    if _INFO_CLIENT is not None:
        return _INFO_CLIENT
    try:
        _INFO_CLIENT = Info(base_url=BASE_URL, skip_ws=True)
        return _INFO_CLIENT
    except Exception:
        return None


def _fetch_symbol_metrics(coin: str) -> Optional[ScanResult]:
    market_symbol = f"{coin}-USDC"
    try:
        info = _get_info_client()
        if info is None:
            return None

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (2 * 24 * 60 * 60 * 1000)
        candles = info.candles_snapshot(
            name=coin,
            interval="5m",
            startTime=start_ms,
            endTime=end_ms,
        )
        if not candles:
            return None
        df = pd.DataFrame(candles)
        if df.empty or len(df) < 40:
            return None

        close = pd.to_numeric(df.get("c"), errors="coerce").dropna()
        high = pd.to_numeric(df.get("h"), errors="coerce").dropna()
        low = pd.to_numeric(df.get("l"), errors="coerce").dropna()
        vol = pd.to_numeric(df.get("v"), errors="coerce").fillna(0.0)
        if close.empty or high.empty or low.empty:
            return None

        ret = close.pct_change().dropna()
        volatility = _safe_float(ret.tail(48).std())  # roughly last 4h of 5m bars
        dollar_volume = _safe_float((close * vol).tail(48).mean())
        sma20 = _safe_float(close.tail(20).mean())
        trend_strength = _safe_float(abs((close.iloc[-1] - sma20) / sma20)) if sma20 > 0 else 0.0
        range_pct = _safe_float(((high.tail(48) - low.tail(48)) / close.tail(48)).mean())

        return ScanResult(
            coin=coin,
            market_symbol=market_symbol,
            score=0.0,
            volatility=volatility,
            dollar_volume=dollar_volume,
            trend_strength=trend_strength,
            range_pct=range_pct,
        )
    except Exception:
        return None


def _minmax_scale(values: List[float]) -> List[float]:
    if not values:
        return []
    vmin = min(values)
    vmax = max(values)
    if vmax <= vmin:
        return [0.0 for _ in values]
    return [(v - vmin) / (vmax - vmin) for v in values]


def run_scan(top_n: int = 5) -> dict:
    universe = _get_universe()
    results: List[ScanResult] = []
    for coin in universe:
        row = _fetch_symbol_metrics(coin)
        if row:
            results.append(row)

    if not results:
        payload = {
            "timestamp": _now_iso(),
            "source": "scanner_hyperliquid",
            "top_n": top_n,
            "rankings": [
                {
                    "rank": 1,
                    "coin": "BTC",
                    "market_symbol": "BTC-USDC",
                    "trade_symbol_hyperliquid": "BTC",
                    "trade_symbol_alpaca": "BTC/USD",
                    "score": 0.0,
                    "reason": "fallback_no_data",
                }
            ],
        }
        RANKINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    vol_scaled = _minmax_scale([r.volatility for r in results])
    dv_scaled = _minmax_scale([r.dollar_volume for r in results])
    trend_scaled = _minmax_scale([r.trend_strength for r in results])
    range_scaled = _minmax_scale([r.range_pct for r in results])

    for idx, row in enumerate(results):
        row.score = (
            (0.35 * vol_scaled[idx])
            + (0.35 * dv_scaled[idx])
            + (0.20 * trend_scaled[idx])
            + (0.10 * range_scaled[idx])
        )

    ranked = sorted(results, key=lambda r: r.score, reverse=True)[: max(1, top_n)]
    payload = {
        "timestamp": _now_iso(),
        "source": "scanner_hyperliquid",
        "top_n": top_n,
        "rankings": [
            {
                "rank": i + 1,
                "coin": row.coin,
                "market_symbol": row.market_symbol,
                "trade_symbol_hyperliquid": row.coin,
                "trade_symbol_alpaca": f"{row.coin}/USD",
                "score": round(row.score, 6),
                "metrics": {
                    "volatility": round(row.volatility, 8),
                    "dollar_volume": round(row.dollar_volume, 2),
                    "trend_strength": round(row.trend_strength, 8),
                    "range_pct": round(row.range_pct, 8),
                },
            }
            for i, row in enumerate(ranked)
        ],
    }
    RANKINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    top_n = int(os.getenv("SCANNER_TOP_N", "5"))
    output = run_scan(top_n=top_n)
    top_coin = output["rankings"][0]["coin"] if output["rankings"] else "BTC"
    print(f"[SCANNER] top coin: {top_coin} | saved: {RANKINGS_PATH}")
