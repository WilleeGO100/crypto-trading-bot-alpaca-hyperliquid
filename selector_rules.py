"""
Hard safety rules for symbol selection.
These rules are always applied before/after any agent decision.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

STATE_FILE = BASE_DIR / "data" / "engine_state.json"

DEFAULT_ALPACA_COINS = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ADA", "XRP"}
DEFAULT_HYPERLIQUID_COINS = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ADA", "XRP"}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _supported_coins(broker: str) -> set[str]:
    env_key = (
        "SUPPORTED_COINS_ALPACA"
        if broker == "alpaca"
        else "SUPPORTED_COINS_HYPERLIQUID"
    )
    raw = os.getenv(env_key, "").strip()
    if raw:
        return {x.strip().upper() for x in raw.split(",") if x.strip()}
    return DEFAULT_ALPACA_COINS if broker == "alpaca" else DEFAULT_HYPERLIQUID_COINS


def _load_engine_state() -> Dict[str, object]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_coin(value: str) -> str:
    raw = (value or "").strip().upper().replace("-", "/")
    if "/" in raw:
        return raw.split("/", 1)[0]
    if raw.endswith("USD") and len(raw) > 3:
        return raw[:-3]
    return raw


def filter_safe_candidates(
    rankings: List[Dict[str, object]],
    broker: str,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    min_score = _safe_float(os.getenv("MIN_SCANNER_SCORE", "0.0"), 0.0)
    min_dollar_volume = _safe_float(os.getenv("MIN_DOLLAR_VOLUME", "10000"), 10000.0)
    min_volatility = _safe_float(os.getenv("MIN_VOLATILITY", "0.00005"), 0.00005)
    max_volatility = _safe_float(os.getenv("MAX_VOLATILITY", "0.02"), 0.02)
    allow_switch_open = (
        os.getenv("ALLOW_SYMBOL_SWITCH_WHILE_OPEN_POSITION", "false").strip().lower()
        == "true"
    )
    supported = _supported_coins(broker)

    state = _load_engine_state()
    open_position = state.get("open_position")
    current_trade_symbol = os.getenv("TRADE_SYMBOL", "").strip()
    pinned_coin = _normalize_coin(current_trade_symbol)
    if open_position and not pinned_coin:
        pinned_coin = "BTC"

    safe: List[Dict[str, object]] = []
    rejected: List[Dict[str, object]] = []

    for entry in rankings:
        coin = _normalize_coin(str(entry.get("coin", "")))
        metrics = entry.get("metrics", {}) if isinstance(entry.get("metrics"), dict) else {}
        score = _safe_float(entry.get("score", 0.0), 0.0)
        volatility = _safe_float(metrics.get("volatility", 0.0), 0.0)
        dollar_volume = _safe_float(metrics.get("dollar_volume", 0.0), 0.0)
        reasons: List[str] = []

        if not coin:
            reasons.append("missing_coin")
        if coin and coin not in supported:
            reasons.append("unsupported_coin")
        if score < min_score:
            reasons.append("score_below_threshold")
        if dollar_volume < min_dollar_volume:
            reasons.append("low_liquidity")
        if volatility < min_volatility:
            reasons.append("volatility_too_low")
        if volatility > max_volatility:
            reasons.append("volatility_too_high")
        if open_position and not allow_switch_open and coin and coin != pinned_coin:
            reasons.append("open_position_symbol_lock")

        if reasons:
            rejected.append(
                {
                    "coin": coin or str(entry.get("coin", "")),
                    "reasons": reasons,
                    "raw": entry,
                }
            )
        else:
            safe.append(entry)

    return safe, rejected


def deterministic_pick(
    broker: str,
    safe_candidates: List[Dict[str, object]],
    reason: str,
) -> Dict[str, str]:
    if safe_candidates:
        best = safe_candidates[0]
        coin = str(best.get("coin", "BTC")).upper()
        market_symbol = str(best.get("market_symbol", f"{coin}-USD")).upper()
        trade_symbol = (
            str(best.get("trade_symbol_alpaca", f"{coin}/USD")).upper()
            if broker == "alpaca"
            else str(best.get("trade_symbol_hyperliquid", coin)).upper()
        )
        return {
            "coin": coin,
            "market_symbol": market_symbol,
            "trade_symbol": trade_symbol,
            "broker": broker,
            "reason": reason,
        }

    fallback_coin = _normalize_coin(os.getenv("TRADE_SYMBOL", "BTC")) or "BTC"
    return {
        "coin": fallback_coin,
        "market_symbol": f"{fallback_coin}-USD",
        "trade_symbol": f"{fallback_coin}/USD" if broker == "alpaca" else fallback_coin,
        "broker": broker,
        "reason": f"{reason}_fallback_default",
    }
