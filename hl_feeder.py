import os
import time
from datetime import datetime
from typing import Optional
import pandas as pd
from dotenv import load_dotenv

# Official Hyperliquid SDK Imports
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL

# Your existing GEX logic
from deribit_gamma import get_btc_gamma_snapshot
from market_data_feeder import LIVE_FEED, atomic_write

load_dotenv()

SYMBOL = os.getenv("TRADE_SYMBOL", "BTC")
SYMBOL_MARKER = LIVE_FEED.with_name("LiveFeed.symbol.txt")

# --- Environment Setup ---
def _resolve_hl_mode() -> str:
    raw_mode = os.getenv("HL_ENVIRONMENT", "").strip().lower()
    if raw_mode in {"paper", "testnet"}:
        return "paper"
    if raw_mode in {"live", "mainnet"}:
        return "live"
    return "paper" if os.getenv("USE_TESTNET", "True").lower() == "true" else "live"


HL_ENV_MODE = _resolve_hl_mode()
IS_TESTNET = HL_ENV_MODE == "paper"
BASE_URL = TESTNET_API_URL if IS_TESTNET else MAINNET_API_URL
GAMMA_REFRESH_SECONDS = float(os.getenv("GAMMA_REFRESH_SECONDS", "120"))

_ohlcv_cache = pd.DataFrame()
_gamma_cache = {
    "snapshot": {
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
    },
    "updated_epoch": 0.0,
}


def _symbol_base_asset(symbol: str) -> str:
    raw = symbol.strip().upper().replace("-", "/")
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if raw.endswith("USDC") and len(raw) > 4:
        raw = raw[:-4]
    if raw.endswith("USD") and len(raw) > 3:
        raw = raw[:-3]
    return raw


def _get_gamma_snapshot_cached() -> dict:
    if _symbol_base_asset(SYMBOL) != "BTC":
        return dict(_gamma_cache["snapshot"])
    now = time.time()
    if now - float(_gamma_cache["updated_epoch"]) >= GAMMA_REFRESH_SECONDS:
        try:
            _gamma_cache["snapshot"] = get_btc_gamma_snapshot()
            _gamma_cache["updated_epoch"] = now
        except Exception as exc:
            print(f"[WARN] Gamma refresh failed: {exc}")
    return dict(_gamma_cache["snapshot"])


def _read_symbol_marker() -> str:
    try:
        if SYMBOL_MARKER.exists():
            return SYMBOL_MARKER.read_text(encoding="utf-8").strip().upper()
    except Exception:
        pass
    return ""


def _write_symbol_marker(symbol: str) -> None:
    try:
        SYMBOL_MARKER.write_text(symbol.strip().upper(), encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] Unable to persist feed symbol marker: {exc}")


def _reset_feed_if_symbol_changed() -> None:
    previous = _read_symbol_marker()
    current = SYMBOL.strip().upper()
    if previous and previous != current:
        print(
            f"[INFO] Feed symbol changed ({previous} -> {current}). "
            "Resetting cached LiveFeed.csv to prevent mixed-price history."
        )
        try:
            if LIVE_FEED.exists():
                LIVE_FEED.unlink()
        except Exception as exc:
            print(f"[WARN] Unable to reset LiveFeed.csv: {exc}")
    _write_symbol_marker(current)


def _load_existing_feed() -> Optional[pd.DataFrame]:
    if not LIVE_FEED.exists():
        return None
    try:
        existing = pd.read_csv(LIVE_FEED)
        if existing.empty:
            return None
        return existing
    except Exception as exc:
        print(f"[WARN] Unable to read existing LiveFeed.csv: {exc}")
        return None


def seed_ohlcv_cache() -> None:
    global _ohlcv_cache
    _reset_feed_if_symbol_changed()
    existing = _load_existing_feed()
    if existing is not None and len(existing) >= 10:
        _ohlcv_cache = existing.tail(150).reset_index(drop=True)
        print(f"[INFO] Resuming cached LiveFeed ({len(_ohlcv_cache)} candles)")
        return

    try:
        info = Info(base_url=BASE_URL, skip_ws=True)
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (6 * 60 * 60 * 1000)  # seed ~6h of 1m candles
        candles = info.candles_snapshot(
            name=SYMBOL,
            interval="1m",
            startTime=start_ms,
            endTime=end_ms,
        )
        if not candles:
            print("[WARN] Hyperliquid seed snapshot returned no candles; continuing without seed.")
            return
        historical = pd.DataFrame(candles)
        if historical.empty:
            print("[WARN] Hyperliquid seed snapshot was empty; continuing without seed.")
            return
        historical = pd.DataFrame(
            {
                "datetime": pd.to_datetime(historical.get("T"), unit="ms", errors="coerce", utc=True),
                "open": pd.to_numeric(historical.get("o"), errors="coerce"),
                "high": pd.to_numeric(historical.get("h"), errors="coerce"),
                "low": pd.to_numeric(historical.get("l"), errors="coerce"),
                "close": pd.to_numeric(historical.get("c"), errors="coerce"),
                "volume": pd.to_numeric(historical.get("v"), errors="coerce").fillna(0.0),
            }
        ).dropna(subset=["datetime", "open", "high", "low", "close"])
    except Exception as exc:
        print(f"[WARN] Unable to seed LiveFeed from Hyperliquid snapshot: {exc}")
        return

    if historical is None or historical.empty:
        print("[WARN] Hyperliquid snapshot returned no candles; continuing without seed.")
        return

    _ohlcv_cache = historical.sort_values("datetime").tail(150).reset_index(drop=True)
    atomic_write(_ohlcv_cache)
    print(f"[INFO] Seeded LiveFeed from Hyperliquid snapshot ({len(_ohlcv_cache)} candles)")


def on_candle_update(msg):
    global _ohlcv_cache
    c = msg.get("data", {})

    new_row = {
        "datetime": pd.to_datetime(c["T"], unit="ms", utc=True),
        "open": float(c["o"]),
        "high": float(c["h"]),
        "low": float(c["l"]),
        "close": float(c["c"]),
        "volume": float(c["v"]),
    }

    gex = _get_gamma_snapshot_cached()
    new_row.update(gex)

    if (
        not _ohlcv_cache.empty
        and _ohlcv_cache.iloc[-1]["datetime"] == new_row["datetime"]
    ):
        _ohlcv_cache.iloc[-1] = pd.Series(new_row)
    else:
        _ohlcv_cache = pd.concat(
            [_ohlcv_cache, pd.DataFrame([new_row])], ignore_index=True
        )

    _ohlcv_cache = _ohlcv_cache.tail(150)
    atomic_write(_ohlcv_cache)

    print(
        f"[OK] [{datetime.now().strftime('%H:%M:%S')}] HL Webhook | {SYMBOL} @ {new_row['close']:.2f} | Gamma: {new_row['gamma_state']}"
    )


def run():
    env_name = "TESTNET (Paper)" if IS_TESTNET else "MAINNET (Live)"
    print(
        f"--- [FEEDER] Hyperliquid WebSocket Online | {env_name} [{SYMBOL}] "
        f"| gamma_refresh={GAMMA_REFRESH_SECONDS}s ---"
    )

    seed_ohlcv_cache()

    while True:
        info = None
        try:
            info = Info(base_url=BASE_URL, skip_ws=False)
            print(f"[*] Subscribing to {SYMBOL} 1m candles...")
            info.subscribe(
                {"type": "candle", "coin": SYMBOL, "interval": "1m"}, on_candle_update
            )

            while info.ws_manager is not None and info.ws_manager.is_alive():
                time.sleep(1)
            raise RuntimeError("websocket manager stopped")
        except KeyboardInterrupt:
            print("\n[!] Shutting down feeder...")
            if info:
                try:
                    info.disconnect_websocket()
                except Exception:
                    pass
            break
        except Exception as exc:
        # Improved logging to show the specific error type and message
                 error_type = exc.__class__.__name__
                 print(f"\n[WARN] feeder websocket disconnected!")
                 print(f"[TYPE] {error_type}: {exc}")
                 print(f"[INFO] Attempting to clean up and reconnect in 10 seconds...\n")

        if info:
            try:
                info.disconnect_websocket()
            except Exception:
                pass
        time.sleep(10)
if __name__ == "__main__":
    run()
