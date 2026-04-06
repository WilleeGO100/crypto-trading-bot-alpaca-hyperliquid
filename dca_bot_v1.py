import os
import time
import requests
import eth_account
import pandas as pd
import math
import traceback
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


def get_sz_decimals(coin, spot_meta):
    if 'universe' in spot_meta:
        for asset in spot_meta['universe']:
            if asset['name'] == coin: return asset['szDecimals']
    return 5 if coin in ['BTC', 'ETH'] else 2


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


def _trend_state(df: pd.DataFrame):
    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    ema50 = df["close"].ewm(span=50, adjust=False).mean()
    latest = float(df["close"].iloc[-1])
    fast = float(ema20.iloc[-1])
    slow = float(ema50.iloc[-1])
    if latest > fast > slow:
        return "LONG"
    if latest < fast < slow:
        return "SHORT"
    return "NEUTRAL"


def _smooth_trend_ok(df: pd.DataFrame) -> bool:
    atr = _atr(df, period=14)
    latest = float(df["close"].iloc[-1])
    if latest <= 0:
        return False
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


def _weighted_avg(prices, qtys) -> float:
    total = sum(qtys)
    if total <= 0:
        return 0.0
    return sum(p * q for p, q in zip(prices, qtys)) / total


def _tp_from_avg(side: str, avg_price: float, target_profit_pct: float) -> float:
    return avg_price * (1.0 + target_profit_pct) if side == "LONG" else avg_price * (1.0 - target_profit_pct)


def _px_round(px: float) -> float:
    if px >= 1000:
        return round(px, 1)
    if px >= 100:
        return round(px, 2)
    if px >= 1:
        return round(px, 3)
    return round(px, 5)


def main():
    # 1. Load from .env and check master switch
    load_dotenv()
    env_mode = os.getenv("HL_ENVIRONMENT", "paper").lower()

    if env_mode == "live":
        secret_key = os.getenv("HL_SECRET_KEY")
        address = os.getenv("HL_ACCOUNT_ADDRESS")
        url = "https://api.hyperliquid.xyz/info"
        api_url = constants.MAINNET_API_URL
    else:
        secret_key = os.getenv("HL_TESTNET_SECRET_KEY")
        address = os.getenv("HL_PAPER_ACCOUNT_ADDRESS")
        url = "https://api.hyperliquid-testnet.xyz/info"
        api_url = constants.TESTNET_API_URL

    if not secret_key or not address:
        print(f"[ERROR] Error: Missing credentials for {env_mode.upper()} mode in the .env file.")
        return

    WATCHLIST = ['BTC', 'ETH', 'SOL', 'ARB', 'TIA']
    BASE_DOLLAR_SIZE = 15.0
    MAX_OPEN_TRADES = 3

    print(f"--- [BOT] Smyrna Station: Fibonacci Scanner ({env_mode.upper()}) ---")

    try:
        spot_meta = requests.post(url, json={"type": "spotMeta"}).json()
        ch_payload = {"type": "clearinghouseState", "user": address}
        ch_state = requests.post(url, json=ch_payload).json()
        active_pos = [p for p in ch_state.get('assetPositions', []) if float(p['position']['szi']) != 0]

        if len(active_pos) >= MAX_OPEN_TRADES:
            print(f"[STOP] Position Limit ({MAX_OPEN_TRADES}) Reached.")
            return
    except Exception as e:
        print(f"[WARN] Sync Error: {e}")
        return

    for coin in WATCHLIST:
        print(f"\n[SCAN] Analyzing {coin}...")
        try:
            # A. Fetch Data
            now = int(time.time() * 1000)
            start = now - (200 * 5 * 60 * 1000)
            payload = {"type": "candleSnapshot",
                       "req": {"coin": coin, "interval": "5m", "startTime": start, "endTime": now}}
            res = requests.post(url, json=payload).json()

            if not res or len(res) < 200: continue

            df = pd.DataFrame(res)
            df['close'], df['high'], df['low'] = df['c'].astype(float), df['h'].astype(float), df['l'].astype(float)

            # B. Indicator Math
            df['EMA_20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()

            ema_20, ema_50, ema_200 = df['EMA_20'].iloc[-1], df['EMA_50'].iloc[-1], df['EMA_200'].iloc[-1]
            s_h, s_l = df['high'].max(), df['low'].min()
            curr_px = df['close'].iloc[-1]
            range_pct = (s_h - s_l) / curr_px

            # C. Logic Gates
            trend_side = _trend_state(df)
            smooth_ok = _smooth_trend_ok(df)
            breakout_ok = _breakout_ok(df, trend_side) if trend_side in {"LONG", "SHORT"} else False

            if not (0.02 <= range_pct <= 0.045):
                print(f"[SKIP] {coin} Volatility ({range_pct * 100:.2f}%) out of zone.")
                continue

            if trend_side == "NEUTRAL":
                print(f"[SKIP] {coin} EMAs tangled.")
                continue

            if not smooth_ok:
                print(f"[SKIP] {coin} Trend not smooth enough.")
                continue

            if not breakout_ok:
                print(f"[SKIP] {coin} No continuation breakout yet.")
                continue

            # --- D. TCL-STYLE ENTRY/EXIT & ASYMMETRICAL STACKING ---
            is_buy = True if trend_side == "LONG" else False
            decimals = get_sz_decimals(coin, spot_meta)
            swing_lookback = int(os.getenv("TCL_SWING_LOOKBACK", "80"))
            recent = df.tail(swing_lookback)
            swing_high, swing_low = float(recent['high'].max()), float(recent['low'].min())
            span = swing_high - swing_low
            if span <= 0:
                print(f"[SKIP] {coin} Invalid swing range.")
                continue

            # Entry is current market context; limits are pullback levels.
            entry_px = _px_round(float(curr_px))
            if is_buy:
                d1_px = _px_round(swing_high - span * 0.382)
                d2_px = _px_round(swing_high - span * 0.618)
            else:
                d1_px = _px_round(swing_low + span * 0.382)
                d2_px = _px_round(swing_low + span * 0.618)

            atr = _atr(df, period=14)
            stop_buffer_atr = float(os.getenv("TCL_STOP_BUFFER_ATR", "0.2"))
            stop_px = _px_round(swing_low - atr * stop_buffer_atr) if is_buy else _px_round(swing_high + atr * stop_buffer_atr)

            account_size = float(ch_state.get('marginSummary', {}).get('withdrawable', BASE_DOLLAR_SIZE))
            account_size_override = float(os.getenv("TCL_ACCOUNT_SIZE_OVERRIDE", "0"))
            if account_size_override > 0:
                account_size = account_size_override
            risk_pct = float(os.getenv("TCL_ACCOUNT_RISK_PCT", "2")) / 100.0
            risk_usd = max(1.0, account_size * risk_pct)

            limits_to_use = int(float(os.getenv("TCL_LIMITS_TO_USE", "2")))
            limits_to_use = max(0, min(2, limits_to_use))
            manage1 = float(os.getenv("TCL_MANAGE_1", "4"))
            manage2 = float(os.getenv("TCL_MANAGE_2", "7.3"))
            weights = [1.0]
            prices = [entry_px]
            if limits_to_use >= 1:
                weights.append(manage1)
                prices.append(d1_px)
            if limits_to_use >= 2:
                weights.append(manage2)
                prices.append(d2_px)

            total_w = sum(weights)
            avg_all = _weighted_avg(prices, weights)
            risk_per_unit = (avg_all - stop_px) if is_buy else (stop_px - avg_all)
            if risk_per_unit <= 0:
                print(f"[SKIP] {coin} Invalid stop distance.")
                continue
            total_qty = risk_usd / risk_per_unit

            p_factor = 10 ** decimals
            qtys = []
            for w in weights:
                q = total_qty * (w / total_w)
                q = math.floor(q * p_factor) / p_factor
                q = max(q, 1 / p_factor)
                qtys.append(q)

            target_profit_pct = float(os.getenv("TCL_TARGET_PROFIT_PCT", "0.015"))
            tp_entry = _px_round(_tp_from_avg(trend_side, entry_px, target_profit_pct))
            tp_limit1 = _px_round(_tp_from_avg(trend_side, _weighted_avg(prices[:2], qtys[:2]), target_profit_pct)) if len(prices) >= 2 else tp_entry
            tp_limit2 = _px_round(_tp_from_avg(trend_side, _weighted_avg(prices, qtys), target_profit_pct))

            print(
                f"[OK] {coin} {trend_side} | Risk: ${risk_usd:.2f} ({risk_pct*100:.2f}%) | "
                f"Entry: ${entry_px} L1: ${d1_px} L2: ${d2_px} Stop: ${stop_px}"
            )
            print(
                f"[MATH] Manage1={manage1} Manage2={manage2} | TP(entry)={tp_entry} "
                f"TP(limit1fill)={tp_limit1} TP(limit2fill)={tp_limit2}"
            )

            # E. Execution
            account = eth_account.Account.from_key(secret_key)
            exchange = Exchange(account, api_url, account_address=address, spot_meta=spot_meta)

            # Entry + asymmetrical add-ons
            exchange.order(coin, is_buy, qtys[0], entry_px, {"limit": {"tif": "Gtc"}})
            if limits_to_use >= 1:
                exchange.order(coin, is_buy, qtys[1], d1_px, {"limit": {"tif": "Gtc"}})
            if limits_to_use >= 2:
                exchange.order(coin, is_buy, qtys[2], d2_px, {"limit": {"tif": "Gtc"}})

            # Exit logic (initial TP from entry win + protective full-size stop)
            exchange.order(coin, not is_buy, qtys[0], tp_entry, {"limit": {"tif": "Gtc", "reduceOnly": True}})
            exchange.order(
                coin,
                not is_buy,
                sum(qtys),
                stop_px,
                {"stopMarket": {"triggerPx": stop_px, "reduceOnly": True}},
            )
            print(f"[START] {coin} TCL Grid Armed on {env_mode.upper()}.")

        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()
