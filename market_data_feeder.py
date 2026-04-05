import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

from deribit_gamma import get_btc_gamma_snapshot

try:
    from alpaca.data.enums import CryptoFeed
    from alpaca.data.historical.crypto import CryptoHistoricalDataClient
    from alpaca.data.live.crypto import CryptoDataStream
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
except Exception:
    CryptoFeed = None
    CryptoHistoricalDataClient = None
    CryptoDataStream = None
    CryptoBarsRequest = None
    TimeFrame = None


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LIVE_FEED = DATA_DIR / "LiveFeed.csv"
TEMP_FEED = DATA_DIR / "LiveFeed.tmp.csv"

FEED_SOURCE = os.getenv("FEED_SOURCE", "alpaca_ws").strip().lower()
SYMBOL = os.getenv("MARKET_SYMBOL", "BTC-USD").strip()
ALPACA_SYMBOL = os.getenv("ALPACA_SYMBOL", os.getenv("TRADE_SYMBOL", "BTC/USD")).strip()
ALPACA_FEED = os.getenv("ALPACA_CRYPTO_FEED", "US").strip().upper()
ALPACA_API_KEY = (
    os.getenv("ALPACA_API_KEY", "").strip()
    or os.getenv("APCA_API_KEY_ID", "").strip()
)
ALPACA_SECRET_KEY = (
    os.getenv("ALPACA_SECRET_KEY", "").strip()
    or os.getenv("APCA_API_SECRET_KEY", "").strip()
)

POLL_SECONDS = float(os.getenv("FEED_POLL_SECONDS", "5"))
HISTORY_LIMIT = int(os.getenv("FEED_HISTORY_LIMIT", "200"))
ALLOW_GAMMA_OVERRIDE = os.getenv("ALLOW_GAMMA_OVERRIDE", "False").strip().lower() == "true"

_raw_use_deribit = os.getenv("USE_DERIBIT_GAMMA", "").strip().lower()
if _raw_use_deribit in {"1", "true", "yes", "on"}:
    USE_DERIBIT_GAMMA = True
elif _raw_use_deribit in {"0", "false", "no", "off"}:
    USE_DERIBIT_GAMMA = False
else:
    # Default behavior: if strategy bypasses gamma gate, skip Deribit dependency.
    USE_DERIBIT_GAMMA = not ALLOW_GAMMA_OVERRIDE

ORDERED_COLS = [
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


def _sanitize_env_secret(value: str) -> str:
    # Helps with accidental quoted values in .env like "KEY" or 'KEY'.
    return value.strip().strip('"').strip("'")


ALPACA_API_KEY = _sanitize_env_secret(ALPACA_API_KEY)
ALPACA_SECRET_KEY = _sanitize_env_secret(ALPACA_SECRET_KEY)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_base_asset() -> str:
    """
    Resolve the base asset ticker used by the current run.
    Examples:
    - BTC/USD -> BTC
    - BTC-USD -> BTC
    - BTC -> BTC
    """
    for raw in (
        os.getenv("TRADE_SYMBOL", ""),
        os.getenv("ALPACA_SYMBOL", ""),
        os.getenv("MARKET_SYMBOL", ""),
    ):
        value = raw.strip().upper()
        if not value:
            continue
        value = value.replace("-", "/")
        if "/" in value:
            return value.split("/", 1)[0]
        if value.endswith("USD") and len(value) > 3:
            return value[:-3]
        return value
    return "BTC"


def _normalize_alpaca_symbol(value: str) -> str:
    raw = value.strip().upper()
    if "/" in raw:
        return raw
    if "-" in raw:
        raw = raw.replace("-", "/")
    if "/" in raw:
        return raw
    if raw.isalpha() and 2 <= len(raw) <= 6:
        return f"{raw}/USD"
    if raw.endswith("USD") and len(raw) > 3:
        return f"{raw[:-3]}/USD"
    return raw


def _ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ORDERED_COLS:
        if col not in out.columns:
            out[col] = None
    return out[ORDERED_COLS]


def _add_gamma(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    base_asset = _extract_base_asset()
    if base_asset == "BTC" and USE_DERIBIT_GAMMA:
        gex = get_btc_gamma_snapshot()
    else:
        # BTC gamma data should not be reused for non-BTC symbols.
        # Also allow intentionally skipping Deribit when gamma is overridden.
        gex = {
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
    for key, value in gex.items():
        out[key] = value
    if "spot_price" not in out.columns:
        out["spot_price"] = out["close"] if "close" in out.columns else None
    return _ensure_schema(out)


def build_placeholder() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "datetime": _iso_utc_now(),
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
            print(f"[WARN] [{datetime.now().strftime('%H:%M:%S')}] yfinance returned empty data.")
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

        out = pd.DataFrame(
            {
                "datetime": pd.to_datetime(df["datetime"], errors="coerce", utc=True),
                "open": pd.to_numeric(df["open"], errors="coerce"),
                "high": pd.to_numeric(df["high"], errors="coerce"),
                "low": pd.to_numeric(df["low"], errors="coerce"),
                "close": pd.to_numeric(df["close"], errors="coerce"),
                "volume": pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0.0),
            }
        )
        out = _add_gamma(out)
        return out.tail(HISTORY_LIMIT).reset_index(drop=True)
    except Exception as exc:
        print(f"[ERROR] Feed build error: {exc}")
        return None


def _replace_target(temp_path: Path, target_path: Path, attempts: int = 5) -> None:
    for n in range(attempts):
        try:
            temp_path.replace(target_path)
            return
        except PermissionError:
            if n == attempts - 1:
                raise
            time.sleep(0.2)


def atomic_write(df: pd.DataFrame) -> None:
    out = _ensure_schema(df)
    out.to_csv(TEMP_FEED, index=False)
    try:
        _replace_target(TEMP_FEED, LIVE_FEED)
    except PermissionError as exc:
        print(f"[WARN] Could not rotate LiveFeed.csv (locked by another process): {exc}")


def ensure_live_feed_exists() -> None:
    if not LIVE_FEED.exists():
        atomic_write(build_placeholder())
        print(f"[INFO] Created placeholder feed: {LIVE_FEED}")


def _bar_get(bar: Any, name: str, default: Any = None) -> Any:
    if isinstance(bar, dict):
        return bar.get(name, default)
    return getattr(bar, name, default)


def _bar_row(bar: Any) -> dict[str, Any]:
    ts = _bar_get(bar, "timestamp")
    ts = pd.to_datetime(ts, errors="coerce", utc=True)
    return {
        "datetime": ts,
        "open": float(_bar_get(bar, "open", 0.0)),
        "high": float(_bar_get(bar, "high", 0.0)),
        "low": float(_bar_get(bar, "low", 0.0)),
        "close": float(_bar_get(bar, "close", 0.0)),
        "volume": float(_bar_get(bar, "volume", 0.0) or 0.0),
    }


def _read_existing_cache() -> pd.DataFrame:
    if not LIVE_FEED.exists():
        return pd.DataFrame(columns=ORDERED_COLS)
    try:
        existing = pd.read_csv(LIVE_FEED)
        if existing.empty:
            return pd.DataFrame(columns=ORDERED_COLS)
        existing["datetime"] = pd.to_datetime(existing.get("datetime"), errors="coerce", utc=True)
        return _ensure_schema(existing).dropna(subset=["datetime"]).tail(HISTORY_LIMIT).reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=ORDERED_COLS)


def _resolve_crypto_feed() -> Any:
    if CryptoFeed is None:
        return None
    if ALPACA_FEED == "GLOBAL":
        return CryptoFeed.GLOBAL
    return CryptoFeed.US


def seed_from_alpaca_history(symbol: str) -> Optional[pd.DataFrame]:
    if (
        CryptoHistoricalDataClient is None
        or CryptoBarsRequest is None
        or TimeFrame is None
    ):
        return None
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return None

    try:
        client = CryptoHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        req = CryptoBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Minute,
            start=datetime.now(timezone.utc) - timedelta(hours=6),
            limit=HISTORY_LIMIT,
        )
        bars = client.get_crypto_bars(req, feed=_resolve_crypto_feed())
        mapping = getattr(bars, "data", None) or bars
        sym_bars = None
        if hasattr(mapping, "get"):
            sym_bars = mapping.get(symbol)
            if sym_bars is None:
                values = list(mapping.values())
                if values:
                    sym_bars = values[0]
        if not sym_bars:
            return None

        rows = [_bar_row(b) for b in sym_bars]
        df = pd.DataFrame(rows)
        if df.empty:
            return None
        return _add_gamma(df).dropna(subset=["datetime"]).tail(HISTORY_LIMIT).reset_index(drop=True)
    except Exception as exc:
        print(f"[WARN] Alpaca history seed failed: {exc}")
        return None


def validate_alpaca_data_access(symbol: str) -> None:
    if (
        CryptoHistoricalDataClient is None
        or CryptoBarsRequest is None
        or TimeFrame is None
    ):
        raise RuntimeError("alpaca-py historical data modules are unavailable.")
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise RuntimeError(
            "ALPACA_API_KEY/ALPACA_SECRET_KEY missing or empty after sanitizing .env values."
        )

    try:
        client = CryptoHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        req = CryptoBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Minute,
            start=datetime.now(timezone.utc) - timedelta(minutes=30),
            limit=1,
        )
        client.get_crypto_bars(req, feed=_resolve_crypto_feed())
    except Exception as exc:
        msg = str(exc)
        raise RuntimeError(
            f"Alpaca auth/entitlement precheck failed for {symbol} on feed={ALPACA_FEED}: {msg}"
        ) from exc


def run_yfinance_loop() -> None:
    print(f"[INFO] yfinance feeder online [{SYMBOL}]")
    print(f"[INFO] Output: {LIVE_FEED}")
    ensure_live_feed_exists()
    try:
        while True:
            df = get_market_frame()
            if df is not None and not df.empty:
                atomic_write(df)
                current_close = float(df["close"].iloc[-1])
                gamma_state = str(df["gamma_state"].iloc[-1])
                print(
                    f"[INFO] [{datetime.now().strftime('%H:%M:%S')}] "
                    f"LiveFeed Updated | {SYMBOL} @ {current_close:.2f} | {gamma_state}"
                )
            else:
                print(f"[WARN] [{datetime.now().strftime('%H:%M:%S')}] feed update skipped")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("[INFO] yfinance feeder stop requested.", flush=True)


def run_alpaca_ws() -> None:
    if CryptoDataStream is None:
        raise RuntimeError("alpaca-py websocket modules are unavailable.")
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise RuntimeError("ALPACA_API_KEY/ALPACA_SECRET_KEY are required for Alpaca websocket feed.")

    symbol = _normalize_alpaca_symbol(ALPACA_SYMBOL)
    validate_alpaca_data_access(symbol)
    print(f"[INFO] Alpaca websocket feeder online [{symbol}] feed={ALPACA_FEED}")
    print(f"[INFO] Output: {LIVE_FEED}")

    cache = _read_existing_cache()
    if cache.empty:
        seeded = seed_from_alpaca_history(symbol)
        if seeded is not None and not seeded.empty:
            cache = seeded
            atomic_write(cache)
            print(f"[INFO] Seeded LiveFeed from Alpaca history ({len(cache)} bars)")
        else:
            cache = build_placeholder()
            atomic_write(cache)

    async def on_bar(bar: Any) -> None:
        nonlocal cache
        row = _bar_row(bar)
        row_df = _add_gamma(pd.DataFrame([row]))

        if cache.empty:
            cache = row_df
        else:
            last_ts = pd.to_datetime(cache.iloc[-1]["datetime"], errors="coerce", utc=True)
            if pd.notna(last_ts) and pd.notna(row["datetime"]) and last_ts == row["datetime"]:
                for col in ORDERED_COLS:
                    cache.at[cache.index[-1], col] = row_df.iloc[0].get(col)
            else:
                cache = pd.concat([cache, row_df], ignore_index=True)

        cache = _ensure_schema(cache).tail(HISTORY_LIMIT).reset_index(drop=True)
        atomic_write(cache)
        current_close = float(cache.iloc[-1]["close"])
        gamma_state = str(cache.iloc[-1]["gamma_state"])
        print(
            f"[INFO] [{datetime.now().strftime('%H:%M:%S')}] "
            f"LiveFeed Updated | {symbol} @ {current_close:.2f} | {gamma_state}"
        )

    stream = CryptoDataStream(
        ALPACA_API_KEY,
        ALPACA_SECRET_KEY,
        feed=_resolve_crypto_feed(),
    )
    stream.subscribe_bars(on_bar, symbol)
    stream.subscribe_updated_bars(on_bar, symbol)

    try:
        stream.run()
    except KeyboardInterrupt:
        print("\n[INFO] stopping Alpaca websocket feeder...")
        stream.stop()
    except Exception as exc:
        raise RuntimeError(f"Alpaca websocket stream failed: {exc}") from exc


def run() -> None:
    print(
        "[CONFIG][FEED] "
        f"source={FEED_SOURCE} | market_symbol={SYMBOL} | alpaca_symbol={ALPACA_SYMBOL} | "
        f"alpaca_feed={ALPACA_FEED} | poll_seconds={POLL_SECONDS} | history_limit={HISTORY_LIMIT} | "
        f"base_asset={_extract_base_asset()} | deribit_gamma_enabled={USE_DERIBIT_GAMMA}"
    )
    source = FEED_SOURCE
    try:
        if source in {"alpaca", "alpaca_ws", "alpaca-websocket"}:
            try:
                run_alpaca_ws()
                return
            except Exception as exc:
                print(
                    "[WARN] Alpaca websocket unavailable (auth/rate-limit/sdk issue). "
                    f"Falling back to yfinance: {exc}"
                )
                run_yfinance_loop()
                return
        run_yfinance_loop()
    except KeyboardInterrupt:
        print("[INFO] Feeder stop requested.", flush=True)


if __name__ == "__main__":
    run()
