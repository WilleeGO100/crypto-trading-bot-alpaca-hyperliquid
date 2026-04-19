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

from main import AGENT_CONFIG, TP, RM, LV, main_loop  # Added RM import
from src.alt_market_analysis_manager import AltMarketAnalysisManager
from src.btc_market_analysis_manager import BTCMarketAnalysisManager


def _base_asset(symbol: str) -> str:
    raw = symbol.strip().upper().replace("-", "/")
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if raw.endswith("USDC") and len(raw) > 4:
        raw = raw[:-4]
    if raw.endswith("USD") and len(raw) > 3:
        raw = raw[:-3]
    return raw or "BTC"


def _sync_runtime_params() -> None:
    max_daily_loss = float(os.getenv("BOT_MAX_DAILY_LOSS", "50"))
    position_size = float(os.getenv("BOT_POSITION_SIZE", "0.001"))
    max_consecutive_losses = int(os.getenv("BOT_MAX_CONSECUTIVE_LOSSES", "5"))
    base = _base_asset(os.getenv("TRADE_SYMBOL", "BTC"))
    is_btc = base == "BTC"

    profile_name = "BTC" if is_btc else "ALT"
    print(f"[PARAM OVERRIDE] Applying {profile_name} parameters... base_asset={base}")

    # Trading params
    trading_params = TP
    if is_btc:
        trading_params["min_gap_size"] = float(os.getenv("BOT_BTC_MIN_GAP_SIZE", "4.0"))
        trading_params["max_gap_age_bars"] = int(float(os.getenv("BOT_BTC_MAX_GAP_AGE_BARS", "80")))
        trading_params["min_risk_reward"] = float(os.getenv("BOT_BTC_MIN_RISK_REWARD", "1.0"))
        trading_params["confidence_threshold"] = float(os.getenv("BOT_BTC_CONFIDENCE_THRESHOLD", "0.4"))
        trading_params["cooldown_seconds"] = float(os.getenv("BOT_BTC_COOLDOWN_SECONDS", "20"))
    else:
        trading_params["min_gap_size"] = float(os.getenv("BOT_ALT_MIN_GAP_SIZE", "0.05"))
        trading_params["max_gap_age_bars"] = int(float(os.getenv("BOT_ALT_MAX_GAP_AGE_BARS", "120")))
        trading_params["min_risk_reward"] = float(os.getenv("BOT_ALT_MIN_RISK_REWARD", "1.0"))
        trading_params["confidence_threshold"] = float(os.getenv("BOT_ALT_CONFIDENCE_THRESHOLD", "0.35"))
        trading_params["cooldown_seconds"] = float(os.getenv("BOT_ALT_COOLDOWN_SECONDS", "20"))
    trading_params["position_size"] = position_size
    AGENT_CONFIG["trading_params"] = trading_params

    # Risk params
    risk_params = RM
    if is_btc:
        risk_params["stop_loss_min"] = float(os.getenv("BOT_BTC_STOP_LOSS_MIN", "12"))
        risk_params["stop_loss_default"] = float(os.getenv("BOT_BTC_STOP_LOSS_DEFAULT", "25"))
        risk_params["stop_loss_max"] = float(os.getenv("BOT_BTC_STOP_LOSS_MAX", "80"))
        risk_params["stop_buffer"] = float(os.getenv("BOT_BTC_STOP_BUFFER", "1.5"))
        risk_params["max_daily_trades"] = int(float(os.getenv("BOT_BTC_MAX_DAILY_TRADES", "15")))
    else:
        risk_params["stop_loss_min"] = float(os.getenv("BOT_ALT_STOP_LOSS_MIN", "0.15"))
        risk_params["stop_loss_default"] = float(os.getenv("BOT_ALT_STOP_LOSS_DEFAULT", "0.50"))
        risk_params["stop_loss_max"] = float(os.getenv("BOT_ALT_STOP_LOSS_MAX", "2.00"))
        risk_params["stop_buffer"] = float(os.getenv("BOT_ALT_STOP_BUFFER", "0.05"))
        risk_params["max_daily_trades"] = int(float(os.getenv("BOT_ALT_MAX_DAILY_TRADES", "20")))
    risk_params["max_daily_loss"] = max_daily_loss
    risk_params["max_consecutive_losses"] = max_consecutive_losses
    AGENT_CONFIG["risk_management"] = risk_params

    # Level context should be symbol-aware as well.
    if is_btc:
        LV["psychological_intervals"] = [int(float(os.getenv("BOT_BTC_LEVEL_INTERVAL", "100")))]
        LV["confluence_tolerance"] = float(os.getenv("BOT_BTC_CONFLUENCE_TOLERANCE", "10.0"))
    else:
        LV["psychological_intervals"] = [int(float(os.getenv("BOT_ALT_LEVEL_INTERVAL", "1")))]
        LV["confluence_tolerance"] = float(os.getenv("BOT_ALT_CONFLUENCE_TOLERANCE", "0.2"))

    print(
        f"[PARAM OVERRIDE] profile={profile_name} position_size={position_size} "
        f"max_daily_loss={max_daily_loss} "
        f"max_consecutive_losses={max_consecutive_losses}"
    )


def main() -> None:
    print("--- MAIN BITCOIN RUNNER ---")
    _sync_runtime_params()
    base = _base_asset(os.getenv("TRADE_SYMBOL", "BTC"))
    analysis_manager_cls = BTCMarketAnalysisManager if base == "BTC" else AltMarketAnalysisManager
    main_loop(analysis_manager_cls=analysis_manager_cls)


if __name__ == "__main__":
    main()
