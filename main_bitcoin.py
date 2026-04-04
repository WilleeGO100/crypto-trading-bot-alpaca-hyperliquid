"""Launches the existing engine loop with Bitcoin as the target symbol."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

os.environ.setdefault("BROKER", "hyperliquid")
broker = os.getenv("BROKER", "").strip().lower()

if broker == "alpaca":
    os.environ.setdefault("TRADE_SYMBOL", "BTC/USD")
    os.environ.setdefault("ALPACA_SYMBOL", "BTC/USD")
else:
    # Hyperliquid perpetuals use the coin ticker, e.g. BTC / ETH / SOL.
    os.environ.setdefault("TRADE_SYMBOL", "BTC")

os.environ.setdefault("MARKET_SYMBOL", "BTC-USD")
os.environ.setdefault("GEX_SYMBOL", "BTC")

if os.getenv("BROKER", "").strip().lower() == "alpaca":
    trade_symbol = os.getenv("TRADE_SYMBOL", "").strip().upper()
    if "/" not in trade_symbol:
        if trade_symbol == "BTC":
            os.environ["TRADE_SYMBOL"] = "BTC/USD"
        elif trade_symbol.endswith("USD") and len(trade_symbol) > 3:
            os.environ["TRADE_SYMBOL"] = f"{trade_symbol[:-3]}/USD"

from main import AGENT_CONFIG, TP, RM, main_loop  # Added RM import
from src.btc_market_analysis_manager import BTCMarketAnalysisManager


def _sync_backtest_params() -> None:
    print("[PARAM OVERRIDE] Applying Bitcoin parameters...")

    # Trading Params
    trading_params = TP
    trading_params["min_gap_size"] = 2.0
    trading_params["max_gap_age_bars"] = 120
    trading_params["min_risk_reward"] = 0.5
    trading_params["confidence_threshold"] = 0.1
    trading_params["cooldown_seconds"] = 2
    AGENT_CONFIG["trading_params"] = trading_params

    # Risk Management (SCALED FOR BITCOIN)
    # Keep stops permissive so paper mode can actually take trades quickly.
    risk_params = RM
    risk_params["stop_loss_min"] = 10
    risk_params["stop_loss_default"] = 30
    risk_params["stop_loss_max"] = 250
    risk_params["stop_buffer"] = 0.5
    risk_params["max_daily_trades"] = 50
    risk_params["max_daily_loss"] = 5000
    risk_params["max_consecutive_losses"] = 20
    AGENT_CONFIG["risk_management"] = risk_params


def main() -> None:
    print("--- MAIN BITCOIN RUNNER ---")
    _sync_backtest_params()
    main_loop(analysis_manager_cls=BTCMarketAnalysisManager)


if __name__ == "__main__":
    main()
