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
from market_data_feeder import LIVE_FEED, atomic_write, get_market_frame

load_dotenv()

SYMBOL = os.getenv("TRADE_SYMBOL", "BTC")
SYMBOL_MARKER = LIVE_FEED.with_name("LiveFeed.symbol.txt")

# --- Environment Setup ---
IS_TESTNET = os.getenv("USE_TESTNET", "True").lower() == "true"
BASE_URL = TESTNET_API_URL if IS_TESTNET else MAINNET_API_URL

_ohlcv_cache = pd.DataFrame()


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
        historical = get_market_frame()
    except Exception as exc:
        print(f"[WARN] Unable to seed LiveFeed from history: {exc}")
        return

    if historical is None or historical.empty:
        print("[WARN] Historical feed returned no candles; continuing without seed.")
        return

    _ohlcv_cache = historical.tail(150).reset_index(drop=True)
    atomic_write(_ohlcv_cache)
    print(f"[INFO] Seeded LiveFeed with {len(_ohlcv_cache)} historical candles")


def on_candle_update(msg):
    global _ohlcv_cache
    c = msg.get("data", {})

    new_row = {
        "datetime": pd.to_datetime(c["T"], unit="ms"),
        "open": float(c["o"]),
        "high": float(c["h"]),
        "low": float(c["l"]),
        "close": float(c["c"]),
        "volume": float(c["v"]),
    }

    gex = get_btc_gamma_snapshot()
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
    print(f"--- [FEEDER] Hyperliquid WebSocket Online | {env_name} [{SYMBOL}] ---")

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
