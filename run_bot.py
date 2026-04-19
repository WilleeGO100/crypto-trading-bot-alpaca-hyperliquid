"""
Launch both the Hyperliquid data feeder and trading engine in parallel.
Streams output from both processes simultaneously into this terminal.
"""

import subprocess
import sys
import time
import threading
from pathlib import Path
import os

from agent_selector import choose_symbol_with_agent
from env_profiles import load_env_profile
from market_scanner import run_scan
from symbol_selector import choose_symbol_for_broker

BASE_DIR = Path(__file__).resolve().parent
LOADED_ENV_PROFILE = load_env_profile("engine")

HYPERLIQUID_ENV = {
    "BOT_PROFILE": "hyperliquid",
    "BROKER": "hyperliquid",
    "HL_MARKET_TYPE": "perp",
    "TRADE_SYMBOL": "BTC",
    "MARKET_SYMBOL": "BTC-USD",
    "GEX_SYMBOL": "BTC",
}

# ANSI escape codes for coloring terminal output
COLOR_FEEDER = "\033[94m"  # Blue
COLOR_ENGINE = "\033[92m"  # Green
COLOR_RESET = "\033[0m"  # Reset


def stream_output(pipe, prefix, color):
    """Reads lines from a subprocess pipe and prints them with a colored prefix."""
    try:
        for line in iter(pipe.readline, ""):
            if line:
                print(f"{color}[{prefix}] {line.strip()}{COLOR_RESET}")
    except ValueError:
        pass  # Pipe closed


def ensure_hyperliquid_execution_env() -> None:
    for key, value in HYPERLIQUID_ENV.items():
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
    selected = {"coin": "BTC", "market_symbol": "BTC-USD", "trade_symbol": "BTC"}
    use_scanner = os.getenv("SCANNER_ENABLED", "true").strip().lower() == "true"
    if not use_scanner:
        print("[SCANNER] Disabled via SCANNER_ENABLED=false. Using BTC defaults.")
        return selected

    try:
        top_n = int(os.getenv("SCANNER_TOP_N", "5"))
        scan = run_scan(top_n=top_n)
        rankings = scan.get("rankings", []) if isinstance(scan, dict) else []
        if rankings:
            top_reason = str(rankings[0].get("reason", "")).strip().lower()
            if top_reason == "fallback_no_data":
                print("[SCANNER] Fallback/no-data ranking detected. Using BTC defaults.")
                print_scanner_metrics(scan)
                return selected
        use_agent = os.getenv("USE_AGENT_SELECTOR", "false").strip().lower() == "true"
        if use_agent:
            selected = choose_symbol_with_agent("hyperliquid")
        else:
            selected = choose_symbol_for_broker("hyperliquid")
        top_coins = [item.get("coin", "?") for item in scan.get("rankings", [])]
        if top_coins:
            print(f"[SCANNER] Top {len(top_coins)}: {', '.join(top_coins)}")
        print_scanner_metrics(scan)
    except Exception as exc:
        print(f"[SCANNER] Failed. Using BTC defaults. reason={exc}")
    return selected


def main():
    print(f"[CONFIG] Loaded env profile: {LOADED_ENV_PROFILE}")
    ensure_hyperliquid_execution_env()
    selected = select_symbol()

    env_snapshot = os.environ.copy()
    env_snapshot["BROKER"] = "hyperliquid"
    env_snapshot["BOT_PROFILE"] = "hyperliquid"
    env_snapshot["TRADE_SYMBOL"] = selected["trade_symbol"]
    env_snapshot["MARKET_SYMBOL"] = selected["market_symbol"]
    env_snapshot["GEX_SYMBOL"] = selected["coin"]

    print("=" * 60)
    print("[START] BOOTING HYPERLIQUID QUANT ENGINE")
    print("=" * 60)
    print(
        f"[INFO] coin={selected['coin']} trade_symbol={env_snapshot['TRADE_SYMBOL']} market_symbol={env_snapshot['MARKET_SYMBOL']}"
    )
    print()

    feeder_process = None
    engine_process = None

    # Use current Python executable (works on both Windows and Linux VMs)
    python_exe = sys.executable

    try:
        # 1. Start the Data Feeder
        print("[1/2] Starting Hyperliquid WebSocket Feeder...")
        feeder_process = subprocess.Popen(
            [python_exe, "hl_feeder.py", "--symbol", selected["trade_symbol"]],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env_snapshot,
        )
        print(f"[OK] Feeder started (PID: {feeder_process.pid})")
        print()

        # Start a background thread to print feeder output immediately
        feeder_thread = threading.Thread(
            target=stream_output,
            args=(feeder_process.stdout, "FEEDER", COLOR_FEEDER),
            daemon=True,
        )
        feeder_thread.start()

        # Wait a moment for the feeder to establish the WebSocket and create LiveFeed.csv
        time.sleep(4)

        # 2. Start the Trading Engine
        print("[2/2] Starting Execution Engine...")
        engine_process = subprocess.Popen(
            [python_exe, "main_bitcoin.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env_snapshot,
        )
        print(f"[OK] Engine started (PID: {engine_process.pid})")
        print()

        print("=" * 60)
        print("[ONLINE] SYSTEM ONLINE. Press Ctrl+C to gracefully shutdown.")
        print("=" * 60)
        print()

        # Start a background thread to print engine output
        engine_thread = threading.Thread(
            target=stream_output,
            args=(engine_process.stdout, "ENGINE", COLOR_ENGINE),
            daemon=True,
        )
        engine_thread.start()

        # Keep the main script alive while the subprocesses run
        feeder_process.wait()
        engine_process.wait()

    except KeyboardInterrupt:
        print(f"\n\n{COLOR_RESET}[STOPPING]  Shutting down safely...")
        if feeder_process:
            feeder_process.terminate()
            print("[OK] Feeder stopped")
        if engine_process:
            engine_process.terminate()
            print("[OK] Engine stopped")
        sys.exit(0)

    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        if feeder_process:
            feeder_process.terminate()
        if engine_process:
            engine_process.terminate()
        sys.exit(1)


if __name__ == "__main__":
    main()
