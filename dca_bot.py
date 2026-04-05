import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv


@dataclass
class DcaLevel:
    level: float
    price: float
    weight: float
    qty: float


def _load_runtime() -> Tuple[str, str, str]:
    load_dotenv()
    use_testnet = os.getenv("USE_TESTNET", "True").strip().lower() == "true"
    info_url = (
        "https://api.hyperliquid-testnet.xyz/info"
        if use_testnet
        else "https://api.hyperliquid.xyz/info"
    )
    trade_symbol = os.getenv("TRADE_SYMBOL", "BTC").strip().upper()
    address = os.getenv("HL_PAPER_ACCOUNT_ADDRESS", "").strip() if use_testnet else os.getenv("HL_ACCOUNT_ADDRESS", "").strip()
    return info_url, trade_symbol, address


def _post(url: str, payload: Dict) -> Dict:
    resp = requests.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _fetch_best_bid_ask(url: str, coin: str) -> Tuple[float, float]:
    book = _post(url, {"type": "l2Book", "coin": coin})
    best_bid = float(book["levels"][0][0]["px"])
    best_ask = float(book["levels"][1][0]["px"])
    return best_bid, best_ask


def _fetch_candles(url: str, coin: str, interval: str, bars: int) -> pd.DataFrame:
    end_ms = int(time.time() * 1000)
    interval_minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}.get(interval, 1)
    start_ms = end_ms - (bars + 20) * interval_minutes * 60 * 1000
    raw = _post(
        url,
        {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        },
    )
    df = pd.DataFrame(raw)
    if df.empty:
        return df
    out = pd.DataFrame(
        {
            "datetime": pd.to_datetime(df["T"], unit="ms", utc=True, errors="coerce"),
            "open": pd.to_numeric(df["o"], errors="coerce"),
            "high": pd.to_numeric(df["h"], errors="coerce"),
            "low": pd.to_numeric(df["l"], errors="coerce"),
            "close": pd.to_numeric(df["c"], errors="coerce"),
            "volume": pd.to_numeric(df["v"], errors="coerce").fillna(0.0),
        }
    )
    return out.dropna().sort_values("datetime").tail(bars).reset_index(drop=True)


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _trend_state(df: pd.DataFrame) -> Tuple[str, float, float]:
    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    ema50 = df["close"].ewm(span=50, adjust=False).mean()
    latest = float(df["close"].iloc[-1])
    fast = float(ema20.iloc[-1])
    slow = float(ema50.iloc[-1])
    if latest > fast > slow:
        return "LONG", fast, slow
    if latest < fast < slow:
        return "SHORT", fast, slow
    return "NEUTRAL", fast, slow


def _smooth_trend_ok(df: pd.DataFrame) -> bool:
    atr = _atr(df, period=14)
    latest = float(df["close"].iloc[-1])
    if latest <= 0:
        return False
    # Reject choppy conditions when ATR is too tiny (dead market) or too large (chaos).
    atr_pct = atr / latest
    return 0.001 <= atr_pct <= 0.03


def _breakout_ok(df: pd.DataFrame, side: str, lookback: int = 20) -> bool:
    if len(df) < lookback + 2:
        return False
    recent = df.tail(lookback + 1)
    latest_close = float(recent["close"].iloc[-1])
    prior_high = float(recent["high"].iloc[:-1].max())
    prior_low = float(recent["low"].iloc[:-1].min())
    if side == "LONG":
        return latest_close > prior_high
    return latest_close < prior_low


def _swing_points(df: pd.DataFrame, lookback: int = 50) -> Tuple[float, float]:
    w = df.tail(lookback)
    return float(w["low"].min()), float(w["high"].max())


def _fib_entries(side: str, swing_low: float, swing_high: float, fib_levels: List[float]) -> List[float]:
    span = swing_high - swing_low
    if span <= 0:
        return []
    prices = []
    if side == "LONG":
        for f in fib_levels:
            prices.append(swing_high - span * f)
    else:
        for f in fib_levels:
            prices.append(swing_low + span * f)
    return prices


def _plan_levels(
    side: str,
    fib_levels: List[float],
    entries: List[float],
    stop_price: float,
    risk_usd: float,
    weights: List[float],
    qty_step: float,
) -> Tuple[List[DcaLevel], float, float]:
    if not entries or risk_usd <= 0:
        return [], 0.0, 0.0
    total_w = sum(weights[: len(entries)])
    if total_w <= 0:
        return [], 0.0, 0.0

    weighted_entry = sum(p * w for p, w in zip(entries, weights)) / total_w
    risk_per_unit = (weighted_entry - stop_price) if side == "LONG" else (stop_price - weighted_entry)
    if risk_per_unit <= 0:
        return [], 0.0, 0.0

    total_qty = risk_usd / risk_per_unit
    levels: List[DcaLevel] = []
    for fib, price, w in zip(fib_levels, entries, weights):
        qty = total_qty * (w / total_w)
        if qty_step > 0:
            qty = max(qty_step, round(qty / qty_step) * qty_step)
        levels.append(DcaLevel(level=float(fib), price=float(price), weight=float(w), qty=float(qty)))
    return levels, total_qty, weighted_entry


def _wallet_withdrawable(url: str, address: str) -> float:
    if not address:
        return 0.0
    try:
        state = _post(url, {"type": "clearinghouseState", "user": address})
        return float(state.get("marginSummary", {}).get("withdrawable", 0.0))
    except Exception:
        return 0.0


def main() -> None:
    info_url, coin, address = _load_runtime()
    best_bid, best_ask = _fetch_best_bid_ask(info_url, coin)
    mark = (best_bid + best_ask) / 2.0

    df = _fetch_candles(info_url, coin, interval=os.getenv("TCL_INTERVAL", "1m"), bars=int(os.getenv("TCL_BARS", "220")))
    if df.empty or len(df) < 80:
        print(f"[TCL] Not enough candle data for {coin}.")
        return

    side, ema_fast, ema_slow = _trend_state(df)
    smooth_ok = _smooth_trend_ok(df)
    breakout_ok = _breakout_ok(df, side) if side in {"LONG", "SHORT"} else False

    print(f"--- TCL Planner ({coin}) ---")
    print(f"Mark={mark:.4f} | Bid={best_bid:.4f} Ask={best_ask:.4f}")
    print(f"Trend={side} | EMA20={ema_fast:.4f} EMA50={ema_slow:.4f}")
    print(f"SmoothTrend={smooth_ok} | Breakout={breakout_ok}")

    if side == "NEUTRAL" or not smooth_ok or not breakout_ok:
        print("[TCL] No valid continuation setup right now.")
        return

    fib_levels_raw = os.getenv("TCL_FIB_LEVELS", "0.382,0.5,0.618")
    fib_levels = [float(x.strip()) for x in fib_levels_raw.split(",") if x.strip()]
    fib_weights_raw = os.getenv("TCL_DCA_WEIGHTS", "1,2,3")
    fib_weights = [float(x.strip()) for x in fib_weights_raw.split(",") if x.strip()]
    if len(fib_weights) < len(fib_levels):
        fib_weights = fib_weights + [fib_weights[-1]] * (len(fib_levels) - len(fib_weights))

    swing_lookback = int(os.getenv("TCL_SWING_LOOKBACK", "80"))
    swing_low, swing_high = _swing_points(df, lookback=swing_lookback)
    entry_prices = _fib_entries(side, swing_low, swing_high, fib_levels)
    if not entry_prices:
        print("[TCL] Unable to compute Fibonacci DCA entries.")
        return

    atr = _atr(df, period=14)
    stop_buffer_atr = float(os.getenv("TCL_STOP_BUFFER_ATR", "0.2"))
    if side == "LONG":
        stop = swing_low - atr * stop_buffer_atr
    else:
        stop = swing_high + atr * stop_buffer_atr

    account_usd = _wallet_withdrawable(info_url, address)
    risk_pct = float(os.getenv("TCL_RISK_PCT", "0.01"))
    risk_usd = max(0.0, account_usd * risk_pct)
    qty_step = float(os.getenv("TCL_QTY_STEP", "0.001"))
    levels, total_qty, avg_entry = _plan_levels(
        side=side,
        fib_levels=fib_levels,
        entries=entry_prices,
        stop_price=stop,
        risk_usd=risk_usd,
        weights=fib_weights,
        qty_step=qty_step,
    )
    if not levels:
        print("[TCL] Invalid sizing plan (risk distance too tight or wallet unavailable).")
        return

    min_rr = float(os.getenv("TCL_MIN_RR", "1.5"))
    if side == "LONG":
        tp = avg_entry + (avg_entry - stop) * min_rr
    else:
        tp = avg_entry - (stop - avg_entry) * min_rr

    notional = total_qty * avg_entry
    leverage = float(os.getenv("HL_DEFAULT_LEVERAGE", "1"))

    print(f"[TCL] Wallet Withdrawable=${account_usd:.2f} | Risk=${risk_usd:.2f} ({risk_pct*100:.2f}%)")
    print(f"[TCL] SwingLow={swing_low:.4f} SwingHigh={swing_high:.4f} ATR14={atr:.4f}")
    print(f"[TCL] Stop={stop:.4f} | AvgEntry={avg_entry:.4f} | TP={tp:.4f} | RR={min_rr:.2f}")
    print(f"[TCL] TotalQty={total_qty:.6f} | Notional=${notional:.2f} | PlannedLev~{leverage:.1f}x")
    for i, lvl in enumerate(levels, start=1):
        print(f"[TCL] DCA{i} fib={lvl.level:.0f} price={lvl.price:.4f} qty={lvl.qty:.6f} weight={lvl.weight:.2f}")
    print("[TCL] Plan generated (no orders sent).")


if __name__ == "__main__":
    main()
