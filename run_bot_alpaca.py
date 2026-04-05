"""
Launch the Alpaca stack in parallel.
Starts market_data_feeder.py + main_bitcoin.py and streams both outputs.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from agent_selector import choose_symbol_with_agent
from market_scanner import run_scan
from symbol_selector import choose_symbol_for_broker

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

ALPACA_ENV = {
    "BOT_PROFILE": "alpaca",
    "BROKER": "alpaca",
    "TRADE_SYMBOL": "BTC/USD",
    "MARKET_SYMBOL": "BTC-USD",
    "GEX_SYMBOL": "BTC",
    "ALPACA_SYMBOL": "BTC/USD",
    "ALPACA_CRYPTO_FEED": "US",
    "FEED_SOURCE": "alpaca_ws",
}

COLOR_FEEDER = "\033[94m"
COLOR_ENGINE = "\033[92m"
COLOR_RESET = "\033[0m"


def stream_output(pipe, prefix, color):
    try:
        for line in iter(pipe.readline, ""):
            if line:
                print(f"{color}[{prefix}] {line.strip()}{COLOR_RESET}")
    except ValueError:
        pass


def ensure_alpaca_execution_env() -> None:
    for key, value in ALPACA_ENV.items():
        os.environ.setdefault(key, value)


def print_scanner_metrics(scan: dict) -> None:
    rankings = scan.get("rankings", []) if isinstance(scan, dict) else []
    if not rankings:
        print("[SCANNER] No rankings to display.")
        return
    print("[SCANNER] Decision metrics:")
    for row in rankings:
        metrics = row.get("metrics", {}) if isinstance(row.get("metrics"), dict) else {}
        rank = row.get("rank", "?")
        coin = row.get("coin", "?")
        score = float(row.get("score", 0.0))
        volatility = float(metrics.get("volatility", 0.0))
        dollar_volume = float(metrics.get("dollar_volume", 0.0))
        trend_strength = float(metrics.get("trend_strength", 0.0))
        range_pct = float(metrics.get("range_pct", 0.0))
        print(
            f"  #{rank} {coin} | score={score:.4f} vol={volatility:.6f} "
            f"dollar_vol={dollar_volume:,.0f} trend={trend_strength:.6f} range={range_pct:.6f}"
        )


def select_symbol() -> dict:
    selected = {"coin": "BTC", "market_symbol": "BTC-USD", "trade_symbol": "BTC/USD"}
    allow_non_btc = (
        os.getenv("ALLOW_NON_BTC_WITH_BITCOIN_ENGINE", "false").strip().lower()
        == "true"
    )
    use_scanner = os.getenv("SCANNER_ENABLED", "true").strip().lower() == "true"
    if not use_scanner:
        print("[SCANNER] Disabled via SCANNER_ENABLED=false. Using BTC defaults.")
        return selected

    try:
        top_n = int(os.getenv("SCANNER_TOP_N", "5"))
        scan = run_scan(top_n=top_n)
        use_agent = os.getenv("USE_AGENT_SELECTOR", "false").strip().lower() == "true"
        if use_agent:
            selected = choose_symbol_with_agent("alpaca")
        else:
            selected = choose_symbol_for_broker("alpaca")
        top_coins = [item.get("coin", "?") for item in scan.get("rankings", [])]
        if top_coins:
            print(f"[SCANNER] Top {len(top_coins)}: {', '.join(top_coins)}")
        print_scanner_metrics(scan)
    except Exception as exc:
        print(f"[SCANNER] Failed. Using BTC defaults. reason={exc}")

    chosen_coin = str(selected.get("coin", "BTC")).strip().upper()
    if chosen_coin != "BTC" and not allow_non_btc:
        print(
            "[SCANNER] Non-BTC selection blocked for main_bitcoin engine "
            f"(selected={chosen_coin}). Using BTC defaults. "
            "Set ALLOW_NON_BTC_WITH_BITCOIN_ENGINE=true to allow this."
        )
        return {"coin": "BTC", "market_symbol": "BTC-USD", "trade_symbol": "BTC/USD"}
    return selected


def main():
    ensure_alpaca_execution_env()
    selected = select_symbol()

    env_snapshot = os.environ.copy()
    env_snapshot["BROKER"] = "alpaca"
    env_snapshot["BOT_PROFILE"] = "alpaca"
    env_snapshot["TRADE_SYMBOL"] = selected["trade_symbol"]
    env_snapshot["ALPACA_SYMBOL"] = selected["trade_symbol"]
    env_snapshot["MARKET_SYMBOL"] = selected["market_symbol"]
    env_snapshot["GEX_SYMBOL"] = selected["coin"]
    env_snapshot.setdefault("FEED_SOURCE", "alpaca_ws")
    env_snapshot["PYTHONUNBUFFERED"] = "1"

    print("=" * 60)
    print("BOOTING ALPACA QUANT ENGINE")
    print("=" * 60)
    print("[INFO] Runtime configuration:")
    print(
        f"  broker={env_snapshot.get('BROKER')} | coin={selected['coin']} | "
        f"trade_symbol={env_snapshot.get('TRADE_SYMBOL')} | "
        f"alpaca_symbol={env_snapshot.get('ALPACA_SYMBOL')} | "
        f"market_symbol={env_snapshot.get('MARKET_SYMBOL')}"
    )
    print(
        f"  feed_source={env_snapshot.get('FEED_SOURCE')} | "
        f"alpaca_crypto_feed={env_snapshot.get('ALPACA_CRYPTO_FEED')} | "
        f"scanner_enabled={os.getenv('SCANNER_ENABLED', 'true')} | "
        f"use_agent_selector={os.getenv('USE_AGENT_SELECTOR', 'false')}"
    )
    print(
        f"  allow_non_btc_with_bitcoin_engine="
        f"{os.getenv('ALLOW_NON_BTC_WITH_BITCOIN_ENGINE', 'false')}"
    )
    print()

    feeder_process = None
    engine_process = None
    python_exe = sys.executable

    try:
        print("[1/2] Starting market data feeder...")
        feeder_process = subprocess.Popen(
            [python_exe, "-u", "market_data_feeder.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env_snapshot,
        )
        print(f"Feeder started (PID: {feeder_process.pid})")
        print()

        feeder_thread = threading.Thread(
            target=stream_output,
            args=(feeder_process.stdout, "FEEDER", COLOR_FEEDER),
            daemon=True,
        )
        feeder_thread.start()

        time.sleep(4)

        print("[2/2] Starting execution engine...")
        engine_process = subprocess.Popen(
            [python_exe, "-u", "main_bitcoin.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env_snapshot,
        )
        print(f"Engine started (PID: {engine_process.pid})")
        print()
        print("=" * 60)
        print("SYSTEM ONLINE. Press Ctrl+C to gracefully shutdown.")
        print("=" * 60)
        print()

        engine_thread = threading.Thread(
            target=stream_output,
            args=(engine_process.stdout, "ENGINE", COLOR_ENGINE),
            daemon=True,
        )
        engine_thread.start()

        feeder_process.wait()
        engine_process.wait()

    except KeyboardInterrupt:
        print(f"\n{COLOR_RESET}Shutting down safely...")
        if feeder_process:
            feeder_process.terminate()
            print("Feeder stopped")
        if engine_process:
            engine_process.terminate()
            print("Engine stopped")
        sys.exit(0)
    except Exception as exc:
        print(f"\nError: {exc}")
        if feeder_process:
            feeder_process.terminate()
        if engine_process:
            engine_process.terminate()
        sys.exit(1)


if __name__ == "__main__":
    main()
