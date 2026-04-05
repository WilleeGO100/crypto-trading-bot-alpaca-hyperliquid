"""Launches the existing engine loop with Bitcoin as the target symbol."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

profile = os.getenv("BOT_PROFILE", "").strip().lower()
if profile == "alpaca":
    os.environ["BROKER"] = "alpaca"
elif profile == "hyperliquid":
    os.environ["BROKER"] = "hyperliquid"
else:
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
    max_daily_loss = float(os.getenv("BOT_MAX_DAILY_LOSS", "50"))
    position_size = float(os.getenv("BOT_POSITION_SIZE", "0.001"))
    max_consecutive_losses = int(os.getenv("BOT_MAX_CONSECUTIVE_LOSSES", "5"))

    # Trading Params (balanced "live-like" profile for paper forward-testing)
    trading_params = TP
    trading_params["min_gap_size"] = 4.0
    trading_params["max_gap_age_bars"] = 80
    trading_params["min_risk_reward"] = 1.0
    trading_params["confidence_threshold"] = 0.4
    trading_params["cooldown_seconds"] = 20
    trading_params["position_size"] = position_size
    AGENT_CONFIG["trading_params"] = trading_params

    # Risk Management (scaled and tighter for live-like behavior)
    risk_params = RM
    risk_params["stop_loss_min"] = 12
    risk_params["stop_loss_default"] = 25
    risk_params["stop_loss_max"] = 80
    risk_params["stop_buffer"] = 1.5
    risk_params["max_daily_trades"] = 15
    risk_params["max_daily_loss"] = max_daily_loss
    risk_params["max_consecutive_losses"] = max_consecutive_losses
    AGENT_CONFIG["risk_management"] = risk_params
    print(
        f"[PARAM OVERRIDE] position_size={position_size} "
        f"max_daily_loss={max_daily_loss} "
        f"max_consecutive_losses={max_consecutive_losses}"
    )


def main() -> None:
    print("--- MAIN BITCOIN RUNNER ---")
    _sync_backtest_params()
    main_loop(analysis_manager_cls=BTCMarketAnalysisManager)


if __name__ == "__main__":
    main()
