import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LIVE_FEED = DATA_DIR / "LiveFeed.csv"
TEMP_FEED = DATA_DIR / "LiveFeed.tmp.csv"

SYMBOL = os.getenv("MARKET_SYMBOL", "NQ=F")
GEX_SYMBOL = os.getenv("GEX_SYMBOL", "SPX")
GEX_API_KEY = os.getenv("GEXBOT_API_KEY", "").strip()
POLL_SECONDS = float(os.getenv("FEED_POLL_SECONDS", "5"))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def classify_gamma_state(
    regime: str,
    spot: float,
    flip: float,
    call_wall: float,
    put_wall: float,
) -> str:
    dist_to_flip = spot - flip
    inside_walls = bool(call_wall and put_wall and put_wall <= spot <= call_wall)

    if abs(dist_to_flip) <= 10:
        return "FLIP_TRANSITION"
    if inside_walls and (call_wall - put_wall) <= 80:
        return "PINNED_INSIDE_WALLS"
    if regime == "NEGATIVE" and spot < flip:
        return "NEG_GAMMA_TREND_DOWN"
    if regime == "NEGATIVE" and spot > flip:
        return "NEG_GAMMA_UNSTABLE_ABOVE_FLIP"
    if regime == "POSITIVE" and inside_walls:
        return "POS_GAMMA_PINNING"
    if regime == "POSITIVE" and spot > flip:
        return "POS_GAMMA_ABOVE_FLIP"
    if regime == "POSITIVE" and spot < flip:
        return "POS_GAMMA_BELOW_FLIP"
    return "UNKNOWN"


def get_gex_snapshot() -> Dict[str, Optional[float]]:
    default = {
        "spot_price": None,
        "gamma_flip": None,
        "major_call_wall": None,
        "major_put_wall": None,
        "market_regime": "UNKNOWN",
        "gamma_state": "UNKNOWN",
        "dist_to_flip": None,
        "dist_to_call_wall": None,
        "dist_to_put_wall": None,
        "inside_walls": None,
    }

    if not GEX_API_KEY:
        return default

    try:
        url = f"https://api.gexbot.com/{GEX_SYMBOL}/classic/zero"
        response = requests.get(url, params={"key": GEX_API_KEY}, timeout=8)
        response.raise_for_status()
        data = response.json()

        spot = _to_float(data.get("spot", data.get("spot_price")))
        flip = _to_float(data.get("gamma_flip", data.get("zero_gamma")))
        call_wall = _to_float(data.get("major_call_wall", data.get("major_pos_vol", data.get("call_wall"))))
        put_wall = _to_float(data.get("major_put_wall", data.get("major_neg_vol", data.get("put_wall"))))

        if spot == 0 or flip == 0:
            return default

        regime = "POSITIVE" if spot > flip else "NEGATIVE"
        dist_to_flip = spot - flip
        dist_to_call_wall = call_wall - spot if call_wall else None
        dist_to_put_wall = spot - put_wall if put_wall else None
        inside_walls = bool(call_wall and put_wall and put_wall <= spot <= call_wall)
        gamma_state = classify_gamma_state(regime, spot, flip, call_wall, put_wall)

        return {
            "spot_price": spot,
            "gamma_flip": flip,
            "major_call_wall": call_wall,
            "major_put_wall": put_wall,
            "market_regime": regime,
            "gamma_state": gamma_state,
            "dist_to_flip": dist_to_flip,
            "dist_to_call_wall": dist_to_call_wall,
            "dist_to_put_wall": dist_to_put_wall,
            "inside_walls": inside_walls,
        }
    except Exception as exc:
        print(f"⚠️ GEX fetch error: {exc}")
        return default


def build_placeholder() -> pd.DataFrame:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    return pd.DataFrame(
        [
            {
                "datetime": now,
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "close": 0.0,
                "volume": 0.0,
                "spot_price": None,
                "gamma_flip": None,
                "major_call_wall": None,
                "major_put_wall": None,
                "market_regime": "UNKNOWN",
                "gamma_state": "UNKNOWN",
                "dist_to_flip": None,
                "dist_to_call_wall": None,
                "dist_to_put_wall": None,
                "inside_walls": None,
            }
        ]
    )


def get_market_frame() -> Optional[pd.DataFrame]:
    try:
        ticker = yf.Ticker(SYMBOL)
        df = ticker.history(period="1d", interval="1m", auto_adjust=False)

        if df.empty:
            print(f"⚠️ [{datetime.now().strftime('%H:%M:%S')}] yfinance returned empty data.")
            return None

        try:
            live_price = ticker.fast_info["last_price"]
        except Exception:
            live_price = float(df["Close"].iloc[-1])

        df.iloc[-1, df.columns.get_loc("Close")] = live_price

        if live_price > float(df.iloc[-1]["High"]):
            df.iloc[-1, df.columns.get_loc("High")] = live_price
        if live_price < float(df.iloc[-1]["Low"]):
            df.iloc[-1, df.columns.get_loc("Low")] = live_price

        df = df.reset_index()
        df.columns = [str(c).strip().lower() for c in df.columns]

        if "datetime" not in df.columns:
            if "date" in df.columns:
                df.rename(columns={"date": "datetime"}, inplace=True)
            elif "index" in df.columns:
                df.rename(columns={"index": "datetime"}, inplace=True)

        gex = get_gex_snapshot()
        for key, value in gex.items():
            df[key] = value

        ordered_cols = [
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "spot_price",
            "gamma_flip",
            "major_call_wall",
            "major_put_wall",
            "market_regime",
            "gamma_state",
            "dist_to_flip",
            "dist_to_call_wall",
            "dist_to_put_wall",
            "inside_walls",
        ]

        for col in ordered_cols:
            if col not in df.columns:
                df[col] = None

        return df[ordered_cols]
    except Exception as exc:
        print(f"🚨 Feed build error: {exc}")
        return None


def atomic_write(df: pd.DataFrame) -> None:
    df.to_csv(TEMP_FEED, index=False)
    TEMP_FEED.replace(LIVE_FEED)


def ensure_live_feed_exists() -> None:
    if not LIVE_FEED.exists():
        atomic_write(build_placeholder())
        print(f"📝 Created placeholder feed: {LIVE_FEED}")


def run() -> None:
    print(f"--- 📡 Feeder Online [{SYMBOL}] ---")
    print(f"📁 Output: {LIVE_FEED}")
    ensure_live_feed_exists()

    while True:
        df = get_market_frame()
        if df is not None and not df.empty:
            atomic_write(df)
            current_close = float(df["close"].iloc[-1])
            gamma_state = str(df["gamma_state"].iloc[-1])
            print(
                f"✅ [{datetime.now().strftime('%H:%M:%S')}] "
                f"LiveFeed Updated | {SYMBOL} @ {current_close:.2f} | {gamma_state}"
            )
        else:
            print(f"⚠️ [{datetime.now().strftime('%H:%M:%S')}] feed update skipped")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()