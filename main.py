import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Type, Tuple

import pandas as pd
from dotenv import load_dotenv

try:
    import eth_account
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL
except Exception:
    eth_account = None
    Exchange = None
    MAINNET_API_URL = ""
    TESTNET_API_URL = ""

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest
except Exception:
    TradingClient = None
    OrderSide = None
    TimeInForce = None
    LimitOrderRequest = None

from src.fvg_analyzer import FVGAnalyzer
from src.level_detector import LevelDetector
from src.market_analysis_manager import MarketAnalysisManager
from src.signal_generator import SignalGenerator

BASE_DIR = Path(__file__).resolve().parent
# Do not override process env so launcher-selected runtime symbols are preserved.
load_dotenv(BASE_DIR / ".env", override=False)

BOT_PROFILE = os.getenv("BOT_PROFILE", "").strip().lower()
if BOT_PROFILE in {"alpaca", "hyperliquid"}:
    # Profile from launcher is authoritative for execution venue.
    os.environ["BROKER"] = BOT_PROFILE

DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

LIVE_FEED = DATA_DIR / "LiveFeed.csv"
STATE_FILE = DATA_DIR / "engine_state.json"
TRADES_FILE = DATA_DIR / "trade_signals.csv"
EXEC_TELEMETRY_FILE = DATA_DIR / "execution_telemetry.csv"

AGENT_CONFIG_PATH = CONFIG_DIR / "agent_config.json"
RISK_RULES_PATH = CONFIG_DIR / "risk_rules.json"

BROKER = os.getenv("BROKER", "alpaca").strip().lower()

# --- Broker Environment Setup ---
IS_TESTNET = os.getenv("USE_TESTNET", "True").lower() == "true"
BASE_URL = TESTNET_API_URL if IS_TESTNET else MAINNET_API_URL
HL_SECRET_KEY = (
    os.getenv("HL_TESTNET_SECRET_KEY", "").strip()
    if IS_TESTNET
    else os.getenv("HL_SECRET_KEY", "").strip()
)

ALPACA_API_KEY = (
    os.getenv("ALPACA_API_KEY", "").strip()
    or os.getenv("APCA_API_KEY_ID", "").strip()
)
ALPACA_SECRET_KEY = (
    os.getenv("ALPACA_SECRET_KEY", "").strip()
    or os.getenv("APCA_API_SECRET_KEY", "").strip()
)
ALPACA_PAPER_TRADE = os.getenv("ALPACA_PAPER_TRADE", "True").lower() == "true"
REQUIRE_GAMMA_IN_PAPER = os.getenv("REQUIRE_GAMMA_IN_PAPER", "False").lower() == "true"


def _sanitize_env_secret(value: str) -> str:
    return value.strip().strip('"').strip("'")


ALPACA_API_KEY = _sanitize_env_secret(ALPACA_API_KEY)
ALPACA_SECRET_KEY = _sanitize_env_secret(ALPACA_SECRET_KEY)
ALPACA_AUTH_BACKOFF_SECONDS = float(os.getenv("ALPACA_AUTH_BACKOFF_SECONDS", "300"))
ALPACA_AUTH_PAUSE_UNTIL = 0.0
ALPACA_LAST_AUTH_LOG_EPOCH = 0.0
POSITION_MAX_HOLD_MINUTES = float(os.getenv("POSITION_MAX_HOLD_MINUTES", "45"))


def _normalize_alpaca_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
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


def _normalize_hyperliquid_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if not raw:
        return "BTC"
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if "-" in raw:
        raw = raw.split("-", 1)[0]
    if raw.endswith("USD") and len(raw) > 3:
        raw = raw[:-3]
    if raw.endswith("USDT") and len(raw) > 4:
        raw = raw[:-4]
    return raw


if BROKER == "alpaca":
    normalized_symbol = _normalize_alpaca_symbol(os.getenv("TRADE_SYMBOL", "BTC/USD"))
    os.environ["TRADE_SYMBOL"] = normalized_symbol
    os.environ["ALPACA_SYMBOL"] = normalized_symbol
elif BROKER == "hyperliquid":
    normalized_symbol = _normalize_hyperliquid_symbol(os.getenv("TRADE_SYMBOL", "BTC"))
    os.environ["TRADE_SYMBOL"] = normalized_symbol

SYMBOL = os.getenv("TRADE_SYMBOL", "BTC/USD")

if BROKER == "hyperliquid":
    if IS_TESTNET:
        print("[TESTNET] ENGINE BOOTING ON HYPERLIQUID PAPER MODE")
    else:
        print("[MAINNET] ENGINE BOOTING ON HYPERLIQUID LIVE MODE")
elif BROKER == "alpaca":
    env_name = "PAPER" if ALPACA_PAPER_TRADE else "LIVE"
    print(f"[ALPACA] ENGINE BOOTING ON {env_name} MODE")
else:
    print(f"[WARNING] Unknown BROKER='{BROKER}'. Trading execution will be skipped.")


def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


AGGRESSIVE_TESTING = os.getenv("USE_AGGRESSIVE_TRADING", "False").lower() == "true"

DEFAULT_TRADING_PARAMS = {
    "min_gap_size": 6.0,
    "max_gap_age_bars": 40,
    "min_risk_reward": 1.5,
    "confidence_threshold": 0.60,
    "position_size": 0.01,
    "cooldown_seconds": 20,
}

AGGRESSIVE_TRADING_PARAMS = {
    "min_gap_size": 2.0,
    "max_gap_age_bars": 120,
    "min_risk_reward": 0.5,
    "confidence_threshold": 0.10,
    "position_size": 0.01,
    "cooldown_seconds": 2,
}

DEFAULT_RISK_MANAGEMENT = {
    "stop_loss_min": 12,
    "stop_loss_default": 20,
    "stop_loss_max": 40,
    "stop_buffer": 2,
    "max_daily_trades": 4,
    "max_daily_loss": 60,
    "max_consecutive_losses": 3,
}

AGGRESSIVE_RISK_MANAGEMENT = {
    "stop_loss_min": 2,
    "stop_loss_default": 8,
    "stop_loss_max": 200,
    "stop_buffer": 0.5,
    "max_daily_trades": 50,
    "max_daily_loss": 5000,
    "max_consecutive_losses": 20,
}

AGENT_CONFIG = load_json(
    AGENT_CONFIG_PATH,
    {
        "trading_params": AGGRESSIVE_TRADING_PARAMS
        if AGGRESSIVE_TESTING
        else DEFAULT_TRADING_PARAMS,
        "risk_management": AGGRESSIVE_RISK_MANAGEMENT
        if AGGRESSIVE_TESTING
        else DEFAULT_RISK_MANAGEMENT,
        "levels": {
            "psychological_intervals": [100],
            "confluence_tolerance": 10.0,
        },
        "logging": {
            "poll_seconds": 2,
        },
    },
)

RISK_RULES = load_json(
    RISK_RULES_PATH,
    {
        "validation": {
            "check_before_signal": True,
        }
    },
)

TP = AGENT_CONFIG["trading_params"]
RM = AGENT_CONFIG["risk_management"]
LV = AGENT_CONFIG["levels"]
LG = AGENT_CONFIG["logging"]

# Optional env safety overrides so both entrypoints (main.py and main_bitcoin.py)
# can enforce identical risk and sizing controls without code edits.
env_position_size = os.getenv("BOT_POSITION_SIZE", "").strip()
if env_position_size:
    TP["position_size"] = float(env_position_size)

env_max_daily_loss = os.getenv("BOT_MAX_DAILY_LOSS", "").strip()
if env_max_daily_loss:
    RM["max_daily_loss"] = float(env_max_daily_loss)

env_max_consecutive_losses = os.getenv("BOT_MAX_CONSECUTIVE_LOSSES", "").strip()
if env_max_consecutive_losses:
    RM["max_consecutive_losses"] = int(env_max_consecutive_losses)

EXECUTION_SLIPPAGE_ALERT_USD = float(os.getenv("EXECUTION_SLIPPAGE_ALERT_USD", "10"))
HL_LEVERAGE_ENABLED = os.getenv("HL_LEVERAGE_ENABLED", "False").lower() == "true"
HL_MARGIN_MODE = os.getenv("HL_MARGIN_MODE", "cross").strip().lower()
HL_DEFAULT_LEVERAGE = int(float(os.getenv("HL_DEFAULT_LEVERAGE", "1")))
HL_MAX_LEVERAGE_CAP = int(float(os.getenv("HL_MAX_LEVERAGE_CAP", "5")))
HL_LEVERAGE_BY_COIN_RAW = os.getenv("HL_LEVERAGE_BY_COIN", "").strip()

POLL_SECONDS = float(LG.get("poll_seconds", 2))


def current_cooldown_seconds() -> float:
    return float(TP.get("cooldown_seconds", 20))

AGGRESSIVE_GAMMA_OVERRIDE = os.getenv("ALLOW_GAMMA_OVERRIDE", "False").lower() == "true"
AUTO_BYPASS_GAMMA_FOR_NON_BTC = (
    os.getenv("AUTO_BYPASS_GAMMA_FOR_NON_BTC", "True").strip().lower() == "true"
)
GAMMA_PRICE_BUFFER = float(os.getenv("GAMMA_PRICE_BUFFER", "5"))
IS_PAPER_MODE = (BROKER == "alpaca" and ALPACA_PAPER_TRADE) or (BROKER == "hyperliquid" and IS_TESTNET)
BYPASS_FVG_REQUIREMENT = os.getenv("BYPASS_FVG_REQUIREMENT", "False").lower() == "true"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_to_epoch(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce", utc=True)
        if pd.isna(ts):
            return None
        return float(ts.timestamp())
    except Exception:
        return None


def _safe_to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _extract_execution_meta(result: Any) -> Dict[str, Any]:
    keys = (
        "id",
        "status",
        "submitted_at",
        "filled_at",
        "filled_avg_price",
        "limit_price",
        "created_at",
        "updated_at",
    )
    out: Dict[str, Any] = {}
    if result is None:
        return out
    if isinstance(result, dict):
        for k in keys:
            if k in result and result[k] is not None:
                out[k] = result[k]
        return out
    for k in keys:
        v = getattr(result, k, None)
        if v is not None:
            out[k] = v
    return out


def _parse_hl_leverage_by_coin(raw: str) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    if not raw:
        return mapping
    for token in raw.split(","):
        item = token.strip()
        if not item or ":" not in item:
            continue
        coin_raw, lev_raw = item.split(":", 1)
        coin = _normalize_hyperliquid_symbol(coin_raw)
        if not coin:
            continue
        try:
            lev = int(float(lev_raw.strip()))
        except Exception:
            continue
        if lev > 0:
            mapping[coin] = lev
    return mapping


HL_LEVERAGE_BY_COIN = _parse_hl_leverage_by_coin(HL_LEVERAGE_BY_COIN_RAW)


def _get_hl_target_leverage(coin: str) -> int:
    normalized = _normalize_hyperliquid_symbol(coin)
    requested = HL_LEVERAGE_BY_COIN.get(normalized, HL_DEFAULT_LEVERAGE)
    capped = min(max(requested, 1), max(HL_MAX_LEVERAGE_CAP, 1))
    return capped


def append_execution_telemetry(row: Dict[str, Any]) -> None:
    try:
        record = pd.DataFrame([row])
        write_header = not EXEC_TELEMETRY_FILE.exists()
        record.to_csv(
            EXEC_TELEMETRY_FILE,
            mode="a",
            index=False,
            header=write_header,
        )
    except Exception as exc:
        print(f"[WARN] Failed to append execution telemetry: {exc}", flush=True)


def default_state() -> Dict[str, Any]:
    return {
        "trading_day": utc_now().strftime("%Y-%m-%d"),
        "daily_trades": 0,
        "daily_loss_points": 0.0,
        "consecutive_losses": 0,
        "last_signal_epoch": 0.0,
        "open_position": None,
        "pause_until_epoch": 0.0,
        "gamma_flip_side": None,
    }


def load_state() -> Dict[str, Any]:
    state = default_state()
    if STATE_FILE.exists():
        try:
            state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass

    today = utc_now().strftime("%Y-%m-%d")
    if state.get("trading_day") != today:
        state = default_state()

    return state


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def normalize_feed(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    if "datetime" in out.columns:
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce", utc=True)
    return out


def extract_gamma_context(df: pd.DataFrame) -> Dict[str, Any]:
    last = df.iloc[-1]

    def value(name: str, default: Any = None) -> Any:
        return last[name] if name in df.columns else default

    def to_float(v: Any) -> Optional[float]:
        try:
            if pd.isna(v):
                return None
            return float(v)
        except Exception:
            return None

    return {
        "spot_price": to_float(value("spot_price", value("close"))),
        "gamma_flip": to_float(value("gamma_flip")),
        "major_call_wall": to_float(value("major_call_wall")),
        "major_put_wall": to_float(value("major_put_wall")),
        "market_regime": str(value("market_regime", "UNKNOWN")).upper(),
        "gamma_state": str(value("gamma_state", "UNKNOWN")).upper(),
        "dist_to_flip": to_float(value("dist_to_flip")),
        "dist_to_call_wall": to_float(value("dist_to_call_wall")),
        "dist_to_put_wall": to_float(value("dist_to_put_wall")),
        "inside_walls": bool(value("inside_walls", False)),
    }


def _gamma_price_side(price: float, flip: Optional[float]) -> Optional[str]:
    if flip is None:
        return None
    if price > flip:
        return "above"
    if price < flip:
        return "below"
    return "at"


def _symbol_base_asset(symbol: str) -> str:
    raw = symbol.strip().upper().replace("-", "/")
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if raw.endswith("USDC") and len(raw) > 4:
        raw = raw[:-4]
    if raw.endswith("USD") and len(raw) > 3:
        raw = raw[:-3]
    return raw


def _gamma_gate_disabled() -> bool:
    if AGGRESSIVE_GAMMA_OVERRIDE:
        return True
    if AUTO_BYPASS_GAMMA_FOR_NON_BTC and _symbol_base_asset(SYMBOL) != "BTC":
        return True
    return False


def _gamma_entry_block_reason(side: str, price: float, gamma_context: Dict[str, Any]) -> Optional[str]:
    gamma_flip = gamma_context.get("gamma_flip")
    gamma_state = str(gamma_context.get("gamma_state", "UNKNOWN")).upper()
    call_wall = gamma_context.get("major_call_wall")
    put_wall = gamma_context.get("major_put_wall")
    gamma_data_available = (
        gamma_flip is not None
        or gamma_state not in {"", "UNKNOWN"}
        or call_wall is not None
        or put_wall is not None
    )

    blockers = []

    if not _gamma_gate_disabled():
        if IS_PAPER_MODE and not REQUIRE_GAMMA_IN_PAPER and not gamma_data_available:
            return None
        if gamma_flip is not None:
            if side == "BUY" and price < gamma_flip - GAMMA_PRICE_BUFFER:
                blockers.append("price below gamma flip")
            elif side == "SELL" and price > gamma_flip + GAMMA_PRICE_BUFFER:
                blockers.append("price above gamma flip")
        else:
            blockers.append("gamma flip unknown")

        if side == "BUY":
            if gamma_state.startswith("NEG"):
                blockers.append("negative gamma state")
            if call_wall and price > call_wall:
                blockers.append("visible call wall above price")
        else:
            if gamma_state.startswith("POS"):
                blockers.append("positive gamma state")
            if put_wall and price < put_wall:
                blockers.append("visible put wall below price")

    if not blockers:
        return None
    return " | ".join(blockers)


def _gamma_exit_trigger(
    side: str, price: float, gamma_context: Dict[str, Any]
) -> Tuple[Optional[str], Optional[float]]:
    gamma_flip = gamma_context.get("gamma_flip")
    gamma_state = str(gamma_context.get("gamma_state", "UNKNOWN")).upper()
    call_wall = gamma_context.get("major_call_wall")
    put_wall = gamma_context.get("major_put_wall")

    if side == "BUY":
        if gamma_state.startswith("NEG") and gamma_flip is not None and price < gamma_flip:
            return "GAMMA_FLIP_NEG", gamma_flip
        if call_wall is not None and price >= call_wall:
            return "CALL_WALL_HIT", call_wall
    else:
        if gamma_state.startswith("POS") and gamma_flip is not None and price > gamma_flip:
            return "GAMMA_FLIP_POS", gamma_flip
        if put_wall is not None and price <= put_wall:
            return "PUT_WALL_HIT", put_wall

    return None, None


def latest_bar_snapshot(df: pd.DataFrame) -> Dict[str, Any]:
    last = df.iloc[-1]
    return {
        "datetime": str(last.get("datetime")),
        "open": float(last["open"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "close": float(last["close"]),
        "volume": float(last.get("volume", 0) or 0),
    }


def build_bypass_setup(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    # Aggressive fallback path when no FVG retest exists.
    if len(df) < 6:
        return None
    closes = pd.to_numeric(df["close"], errors="coerce")
    highs = pd.to_numeric(df["high"], errors="coerce")
    lows = pd.to_numeric(df["low"], errors="coerce")
    if closes.isna().any() or highs.isna().any() or lows.isna().any():
        return None

    current_price = float(closes.iloc[-1])
    drift = float(closes.iloc[-1] - closes.iloc[-4])
    if abs(drift) < 1e-9:
        return None

    side = "BUY" if drift > 0 else "SELL"
    if side == "BUY":
        zone_bottom = float(lows.tail(5).min())
        zone_top = current_price
    else:
        zone_top = float(highs.tail(5).max())
        zone_bottom = current_price

    return {
        "side": side,
        "zone_top": zone_top,
        "zone_bottom": zone_bottom,
        "gap_size": abs(drift),
        "created_bar_index": len(df) - 1,
        "setup_age_bars": 0,
        "entry_price": current_price,
        "reason": "BYPASS_FVG_REQUIREMENT momentum fallback",
    }


def build_trade_plan(
    side: str,
    entry_price: float,
    setup: Dict[str, Any],
    level_context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    stop_buffer = float(RM.get("stop_buffer", 2))
    min_stop = float(RM.get("stop_loss_min", 2))
    default_stop = float(RM.get("stop_loss_default", 20))
    max_stop = float(RM.get("stop_loss_max", 200))
    min_rr = float(TP.get("min_risk_reward", 0.5))

    resistance = level_context.get("nearest_structural_resistance")
    support = level_context.get("nearest_structural_support")

    def _debug_failure(reason: str, **details: Any) -> None:
        print(
            f"[PLAN FAIL] side={side} entry={entry_price:.2f} reason={reason} "
            f"stop_buffer={stop_buffer} min_stop={min_stop} max_stop={max_stop} min_rr={min_rr} "
            f"support={support} resistance={resistance} "
            f"details={details}"
        )

    if side == "BUY":
        natural_stop = (
            min(
                float(setup["zone_bottom"]),
                float(support) if support is not None else entry_price - default_stop,
            )
            - stop_buffer
        )
        stop_loss = natural_stop
        risk_points = entry_price - stop_loss
        if risk_points < min_stop:
            stop_loss = entry_price - min_stop
            risk_points = min_stop
        if risk_points > max_stop:
            _debug_failure(
                "risk_points_above_max",
                risk_points=risk_points,
                stop_loss=stop_loss,
            )
            return None

        target_price = entry_price + (risk_points * min_rr)
        reward_points = target_price - entry_price

        if resistance is not None and (float(resistance) - entry_price) < reward_points:
            _debug_failure(
                "insufficient_room_to_resistance",
                reward_points=reward_points,
                resistance=float(resistance) - entry_price,
            )
            return None

    else:
        natural_stop = (
            max(
                float(setup["zone_top"]),
                (
                    float(resistance)
                    if resistance is not None
                    else entry_price + default_stop
                ),
            )
            + stop_buffer
        )
        stop_loss = natural_stop
        risk_points = stop_loss - entry_price
        if risk_points < min_stop:
            stop_loss = entry_price + min_stop
            risk_points = min_stop
        if risk_points > max_stop:
            _debug_failure(
                "risk_points_above_max",
                risk_points=risk_points,
                stop_loss=stop_loss,
            )
            return None

        target_price = entry_price - (risk_points * min_rr)
        reward_points = entry_price - target_price

        if support is not None and (entry_price - float(support)) < reward_points:
            _debug_failure(
                "insufficient_room_to_support",
                reward_points=reward_points,
                support=float(entry_price - float(support)),
            )
            return None

    return {
        "entry_price": entry_price,
        "stop_loss": round(stop_loss, 2),
        "target_price": round(target_price, 2),
        "risk_points": round(risk_points, 2),
        "reward_points": round(reward_points, 2),
        "risk_reward": (
            round(reward_points / risk_points, 2) if risk_points > 0 else 0.0
        ),
    }


def can_trade(state: Dict[str, Any]) -> Optional[str]:
    now_epoch = time.time()

    if state.get("pause_until_epoch", 0.0) > now_epoch:
        return "engine paused after max consecutive losses"
    if state["daily_trades"] >= int(RM.get("max_daily_trades", 4)):
        return "max daily trades reached"
    if state["daily_loss_points"] >= float(RM.get("max_daily_loss", 60)):
        return "max daily loss reached"
    if state["consecutive_losses"] >= int(RM.get("max_consecutive_losses", 3)):
        pause_minutes = 60
        state["pause_until_epoch"] = now_epoch + (pause_minutes * 60)
        save_state(state)
        return "max consecutive losses reached"
    if now_epoch - float(state.get("last_signal_epoch", 0.0)) < current_cooldown_seconds():
        return "cooldown active"
    if state.get("open_position"):
        return "position already open"
    return None


def execute_hl_trade(action: str, coin: str, price: float, sz: float) -> Optional[Any]:
    if not HL_SECRET_KEY:
        print("[WARNING] No HL secret key found in .env. Hyperliquid trade skipped.")
        return None
    if eth_account is None or Exchange is None:
        print("[ERROR] Hyperliquid SDK is unavailable. Install hyperliquid-python-sdk.")
        return None

    try:
        account = eth_account.Account.from_key(HL_SECRET_KEY)
        exchange = Exchange(
            wallet=account, base_url=BASE_URL, spot_meta={"universe": [], "tokens": []}
        )

        is_buy = action.upper() in ["BUY", "EXIT_SHORT"]
        print(
            f"[EXECUTE][HYPERLIQUID] {action.upper()} {sz} {coin} at ~{price}"
        )

        if "EXIT" in action.upper():
            result = exchange.market_close(coin=coin, sz=sz, px=price)
        else:
            if HL_LEVERAGE_ENABLED:
                leverage = _get_hl_target_leverage(coin)
                is_cross = HL_MARGIN_MODE != "isolated"
                try:
                    exchange.update_leverage(leverage, coin, is_cross=is_cross)
                    print(
                        "[LEVERAGE] "
                        f"coin={coin} mode={'cross' if is_cross else 'isolated'} "
                        f"applied={leverage}x"
                    )
                except Exception as lev_exc:
                    print(
                        "[WARN] Failed to update leverage before entry: "
                        f"coin={coin} err={lev_exc}"
                    )
            result = exchange.market_open(name=coin, is_buy=is_buy, sz=sz, px=price)
        print(f"[SUCCESS][HYPERLIQUID] {result}")
        # Some SDK close paths can return None even when the request succeeds.
        if result is None:
            return {"status": "ok", "response": None}
        return result
    except Exception as exc:
        print(f"[ERROR] Hyperliquid execution failed: {exc}")
        return None


def execute_alpaca_trade(action: str, symbol: str, price: float, sz: float) -> Optional[Any]:
    global ALPACA_AUTH_PAUSE_UNTIL, ALPACA_LAST_AUTH_LOG_EPOCH

    if (
        TradingClient is None
        or OrderSide is None
        or TimeInForce is None
        or LimitOrderRequest is None
    ):
        print("[ERROR] Alpaca SDK unavailable. Install dependencies from requirements.txt.")
        return None

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("[WARNING] Missing ALPACA_API_KEY/ALPACA_SECRET_KEY. Alpaca trade skipped.")
        return None

    now_epoch = time.time()
    if ALPACA_AUTH_PAUSE_UNTIL > now_epoch:
        remaining = int(ALPACA_AUTH_PAUSE_UNTIL - now_epoch)
        if now_epoch - ALPACA_LAST_AUTH_LOG_EPOCH >= 30:
            print(
                f"[WARN] Alpaca execution paused after auth failure. "
                f"retry_in={remaining}s",
                flush=True,
            )
            ALPACA_LAST_AUTH_LOG_EPOCH = now_epoch
        return None

    try:
        symbol = _normalize_alpaca_symbol(symbol)
        client = TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=ALPACA_PAPER_TRADE,
        )
        side = (
            OrderSide.BUY
            if action.upper() in ["BUY", "EXIT_SHORT"]
            else OrderSide.SELL
        )
        order = LimitOrderRequest(
            symbol=symbol,
            qty=float(sz),
            side=side,
            limit_price=round(float(price), 2),
            time_in_force=TimeInForce.GTC,
        )
        print(f"[EXECUTE][ALPACA] {action.upper()} {sz} {symbol} LIMIT @{round(float(price), 2)}")
        result = client.submit_order(order_data=order)
        print(
            f"[SUCCESS][ALPACA] order_id={getattr(result, 'id', None)} status={getattr(result, 'status', None)}"
        )
        return result
    except Exception as exc:
        msg = str(exc)
        print(f"[ERROR] Alpaca execution failed: {msg}")
        if "unauthorized" in msg.lower():
            ALPACA_AUTH_PAUSE_UNTIL = time.time() + ALPACA_AUTH_BACKOFF_SECONDS
            ALPACA_LAST_AUTH_LOG_EPOCH = 0.0
            print(
                "[HINT] Alpaca rejected credentials. Verify API key/secret, "
                "paper vs live mode, and that trading permissions are enabled. "
                f"Execution paused for {int(ALPACA_AUTH_BACKOFF_SECONDS)}s before retry.",
                flush=True,
            )
        return None


def execute_trade(action: str, symbol: str, price: float, sz: float) -> Optional[Any]:
    if BROKER == "alpaca":
        return execute_alpaca_trade(action=action, symbol=symbol, price=price, sz=sz)
    if BROKER == "hyperliquid":
        return execute_hl_trade(action=action, coin=symbol, price=price, sz=sz)
    print(f"[WARNING] Unsupported broker '{BROKER}'. Trade execution skipped.")
    return None


def maybe_exit_open_position(
    df: pd.DataFrame,
    state: Dict[str, Any],
    signal_writer: SignalGenerator,
    gamma_context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    position = state.get("open_position")
    if not position:
        return None

    last = df.iloc[-1]
    high = float(last["high"])
    low = float(last["low"])
    close = float(last["close"])

    side = position["side"]
    stop_loss = float(position["stop_loss"])
    target_price = float(position["target_price"])
    entry_price = float(position["entry_price"])
    entry_epoch = float(position.get("entry_epoch", 0.0) or 0.0)

    # Retrieve the exact size we entered with so we can close it fully
    sz = float(position.get("size", TP.get("position_size", 0.01)))

    exit_reason = None
    exit_price = None

    gamma_reason, gamma_price = _gamma_exit_trigger(side, close, gamma_context)
    if gamma_reason:
        exit_reason = gamma_reason
        exit_price = gamma_price or close

    if exit_reason is None:
        if entry_epoch > 0 and POSITION_MAX_HOLD_MINUTES > 0:
            held_minutes = (time.time() - entry_epoch) / 60.0
            if held_minutes >= POSITION_MAX_HOLD_MINUTES:
                exit_reason = "TIME_EXIT"
                exit_price = close

    if exit_reason is None:
        if side == "BUY":
            if low <= stop_loss:
                exit_reason = "STOP_LOSS"
                exit_price = stop_loss
            elif high >= target_price:
                exit_reason = "TARGET_HIT"
                exit_price = target_price
        else:
            if high >= stop_loss:
                exit_reason = "STOP_LOSS"
                exit_price = stop_loss
            elif low <= target_price:
                exit_reason = "TARGET_HIT"
                exit_price = target_price

    if exit_reason is None:
        return None

    # Execute the exit on the blockchain
    action = "EXIT_LONG" if side == "BUY" else "EXIT_SHORT"
    exit_result = execute_trade(action=action, symbol=SYMBOL, price=exit_price, sz=sz)
    if exit_result is None:
        print(
            "[WARN] Exit trigger hit but broker execution failed. "
            "Keeping open_position unchanged so state does not desync.",
            flush=True,
        )
        return None

    pnl_points = (
        (exit_price - entry_price) if side == "BUY" else (entry_price - exit_price)
    )

    state["open_position"] = None
    if pnl_points > 0:
        state["consecutive_losses"] = 0
    else:
        state["consecutive_losses"] += 1
        state["daily_loss_points"] += abs(float(pnl_points))

    timestamp = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    signal_writer.append_signal(
        timestamp=timestamp,
        direction=action,
        entry_price=round(exit_price, 2),
        stop_loss=stop_loss,
        take_profit=target_price,
    )
    save_state(state)

    return {
        "exit_reason": exit_reason,
        "exit_price": round(exit_price, 2),
        "pnl_points": round(pnl_points, 2),
        "last_close": close,
    }


def make_market_snapshot(
    analysis_manager: Any,
    latest_bar: Dict[str, Any],
    gamma_context: Dict[str, Any],
    level_context: Dict[str, Any],
    setup: Optional[Dict[str, Any]],
    decision: Dict[str, Any],
    engine_state: Dict[str, Any],
) -> Dict[str, Any]:
    fallback = {
        "timestamp": utc_now().isoformat(),
        "latest_bar": latest_bar,
        "gamma_context": gamma_context,
        "level_context": level_context,
        "setup": setup,
        "decision": decision,
        "engine_state": engine_state or {},
    }
    builder = getattr(analysis_manager, "build_market_snapshot", None)
    if callable(builder):
        try:
            return builder(
                latest_bar=latest_bar,
                gamma_context=gamma_context,
                level_context=level_context,
                setup=setup,
                decision=decision,
                engine_state=engine_state,
            )
        except Exception:
            pass
    return fallback


def main_loop(
    analysis_manager_cls: Type[MarketAnalysisManager] = MarketAnalysisManager,
    analysis_file: Optional[str] = None,
) -> None:
    print("--- QUANT ENGINE LIVE LOOP ONLINE ---")
    print(f"[FEED] Monitoring: {LIVE_FEED}")
    print(f"[MANAGER] Analysis: {analysis_manager_cls.__name__}")
    print(
        "[CONFIG][ENGINE] "
        f"broker={BROKER} | bot_profile={BOT_PROFILE or 'none'} | symbol={SYMBOL} | "
        f"alpaca_paper_trade={ALPACA_PAPER_TRADE} | "
        f"use_testnet={IS_TESTNET} | poll_seconds={POLL_SECONDS} | "
        f"alpaca_auth_backoff_seconds={ALPACA_AUTH_BACKOFF_SECONDS} | "
        f"position_max_hold_minutes={POSITION_MAX_HOLD_MINUTES} | "
        f"bypass_fvg_requirement={BYPASS_FVG_REQUIREMENT}"
    )
    print(f"[CONFIG][TP] {json.dumps(TP, sort_keys=True)}")
    print(f"[CONFIG][RM] {json.dumps(RM, sort_keys=True)}")
    print(f"[CONFIG][LV] {json.dumps(LV, sort_keys=True)}")
    print(f"[CONFIG][LG] {json.dumps(LG, sort_keys=True)}")
    print(
        "[CONFIG][LEVERAGE] "
        f"enabled={HL_LEVERAGE_ENABLED} | "
        f"margin_mode={HL_MARGIN_MODE} | "
        f"default={HL_DEFAULT_LEVERAGE}x | "
        f"max_cap={HL_MAX_LEVERAGE_CAP}x | "
        f"by_coin={json.dumps(HL_LEVERAGE_BY_COIN, sort_keys=True)}"
    )
    print(
        "[CONFIG][EFFECTIVE_SAFETY] "
        f"position_size={TP.get('position_size')} | "
        f"max_daily_loss={RM.get('max_daily_loss')} | "
        f"max_consecutive_losses={RM.get('max_consecutive_losses')} | "
        f"slippage_alert_usd={EXECUTION_SLIPPAGE_ALERT_USD}"
    )
    print()

    # Log trade thresholds at startup
    min_rr = float(TP.get("min_risk_reward", 1.5))
    conf_thresh = float(TP.get("confidence_threshold", 0.60))
    print(f"[CONFIG] confidence_threshold={conf_thresh} | min_risk_reward={min_rr}")
    print(
        "[CONFIG] "
        f"stop_loss_min={RM.get('stop_loss_min')} | "
        f"stop_loss_default={RM.get('stop_loss_default')} | "
        f"stop_loss_max={RM.get('stop_loss_max')} | "
        f"cooldown_seconds={current_cooldown_seconds()} | "
        f"aggressive_gamma_override={AGGRESSIVE_GAMMA_OVERRIDE} | "
        f"auto_bypass_gamma_for_non_btc={AUTO_BYPASS_GAMMA_FOR_NON_BTC} | "
        f"effective_gamma_gate={'DISABLED' if _gamma_gate_disabled() else 'ENABLED'} | "
        f"require_gamma_in_paper={REQUIRE_GAMMA_IN_PAPER}"
    )
    print()

    analyzer = FVGAnalyzer(
        min_gap_size=float(TP.get("min_gap_size", 6.0)),
        max_gap_age_bars=int(TP.get("max_gap_age_bars", 40)),
    )
    detector = LevelDetector(
        level_intervals=list(LV.get("psychological_intervals", [100])),
        confluence_tolerance=float(LV.get("confluence_tolerance", 10.0)),
    )
    if analysis_file is None:
        analysis_manager = analysis_manager_cls()
    else:
        analysis_manager = analysis_manager_cls(analysis_file)
    signal_writer = SignalGenerator(TRADES_FILE)

    state = load_state()
    last_wait_signature = ""
    last_no_setup_signature = ""
    last_status_print = time.time()
    STATUS_THROTTLE = 120

    while True:
        try:
            if not LIVE_FEED.exists():
                print("[WAIT] waiting for LiveFeed.csv ...", flush=True)
                time.sleep(POLL_SECONDS)
                continue

            df = pd.read_csv(LIVE_FEED)
            df = normalize_feed(df)

            required = {"open", "high", "low", "close"}
            if not required.issubset(df.columns):
                print("[ERROR] missing OHLC columns", flush=True)
                time.sleep(POLL_SECONDS)
                continue

            if len(df) < 10:
                print(f"[WAIT] buffering data ({len(df)}/10 candles)...", flush=True)
                time.sleep(POLL_SECONDS)
                continue

            state = load_state()
            gamma_context = extract_gamma_context(df)
            latest_bar = latest_bar_snapshot(df)
            current_price = float(df["close"].iloc[-1])
            state["gamma_flip_side"] = _gamma_price_side(
                current_price, gamma_context.get("gamma_flip")
            )

            # Show current bar only every 2 minutes (throttled)
            now = time.time()
            if now - last_status_print >= STATUS_THROTTLE:
                latest_time = (
                    df["datetime"].iloc[-1] if "datetime" in df.columns else "?"
                )
                print(
                    f"[{latest_time}] price={current_price:.2f} gamma={gamma_context.get('gamma_state')} regime={gamma_context.get('market_regime')}",
                    flush=True,
                )
                last_status_print = now

            exit_info = maybe_exit_open_position(df, state, signal_writer, gamma_context)
            if exit_info:
                print(f"[EXIT] {exit_info}", flush=True)

            block_reason = can_trade(state)
            if block_reason:
                snapshot = make_market_snapshot(
                    analysis_manager=analysis_manager,
                    latest_bar=latest_bar,
                    gamma_context=gamma_context,
                    level_context={},
                    setup=None,
                    decision={"status": "WAIT", "reason": block_reason},
                    engine_state=state,
                )
                try:
                    analysis_manager.save_analysis(snapshot)
                except Exception as e:
                    print(f"[!] Failed to save analysis: {e}", flush=True)
                time.sleep(POLL_SECONDS)
                continue

            setup_obj = analyzer.find_setup(df.tail(150))
            setup: Optional[Dict[str, Any]] = None
            if setup_obj:
                setup = setup_obj.to_dict()
            elif BYPASS_FVG_REQUIREMENT:
                setup = build_bypass_setup(df.tail(150))

            if not setup:
                no_setup_reason = (
                    "no valid FVG retest or fallback setup"
                    if BYPASS_FVG_REQUIREMENT
                    else "no valid FVG retest"
                )
                snapshot = make_market_snapshot(
                    analysis_manager=analysis_manager,
                    latest_bar=latest_bar,
                    gamma_context=gamma_context,
                    level_context={},
                    setup=None,
                    decision={"status": "WAIT", "reason": no_setup_reason},
                    engine_state=state,
                )
                try:
                    analysis_manager.save_analysis(snapshot)
                except Exception as e:
                    print(f"[!] Failed to save analysis: {e}", flush=True)
                no_setup_sig = "|".join(
                    (
                        str(latest_bar.get("datetime")),
                        str(gamma_context.get("gamma_state", "UNKNOWN")),
                        str(gamma_context.get("market_regime", "UNKNOWN")),
                    )
                )
                if no_setup_sig != last_no_setup_signature:
                    print(
                        f"[WAIT] {no_setup_reason} "
                        f"| bypass_fvg_requirement={BYPASS_FVG_REQUIREMENT} "
                        f"| gamma_gate={'DISABLED' if _gamma_gate_disabled() else 'ENABLED'} "
                        f"| gamma={gamma_context.get('gamma_state')} "
                        f"| regime={gamma_context.get('market_regime')}",
                        flush=True,
                    )
                    last_no_setup_signature = no_setup_sig
                time.sleep(POLL_SECONDS)
                continue

            level_context = detector.analyze_level_context(
                current_price=current_price, gamma_context=gamma_context
            )
            confluence = detector.score_confluence(
                side=setup["side"],
                entry_price=current_price,
                level_context=level_context,
                gamma_context=gamma_context,
            )
            confidence = round(confluence["score"], 2)

            trade_plan = build_trade_plan(
                side=setup["side"],
                entry_price=current_price,
                setup=setup,
                level_context=level_context,
            )

            decision: Dict[str, Any] = {
                "status": "WAIT",
                "reason": "",
                "confidence": confidence,
                "confluence_reasons": confluence["reasons"],
            }

            # Build detailed decision log
            conf_thresh = float(TP.get("confidence_threshold", 0.60))
            min_rr = float(TP.get("min_risk_reward", 1.5))

            if confidence < conf_thresh:
                decision["reason"] = (
                    f"confidence {confidence:.2f} < threshold {conf_thresh}"
                )
            elif trade_plan is None:
                decision["reason"] = "invalid stop/target or insufficient room"
            elif trade_plan["risk_reward"] < min_rr:
                decision["reason"] = (
                    f"risk_reward {trade_plan['risk_reward']:.2f} < threshold {min_rr}"
                )
            else:
                decision["status"] = "READY"
                decision["reason"] = "setup passed all filters"
                decision["trade_plan"] = trade_plan

            gamma_block_reason = _gamma_entry_block_reason(
                setup["side"], current_price, gamma_context
            )
            if gamma_block_reason:
                existing_reason = decision.get("reason", "")
                combined = (
                    f"{existing_reason} | {gamma_block_reason}"
                    if existing_reason
                    else gamma_block_reason
                )
                decision["status"] = "WAIT"
                decision["reason"] = combined
                decision.pop("trade_plan", None)

            # Alpaca crypto spot is effectively long-only for this strategy flow:
            # opening a fresh SELL without holdings will be rejected as insufficient balance.
            if BROKER == "alpaca" and setup["side"] == "SELL":
                existing_reason = decision.get("reason", "")
                spot_reason = "alpaca spot long-only mode: skipping SELL entry setup"
                decision["status"] = "WAIT"
                decision["reason"] = (
                    f"{existing_reason} | {spot_reason}" if existing_reason else spot_reason
                )
                decision.pop("trade_plan", None)

            snapshot = make_market_snapshot(
                analysis_manager=analysis_manager,
                latest_bar=latest_bar,
                gamma_context=gamma_context,
                level_context=level_context,
                setup=setup,
                decision=decision,
                engine_state=state,
            )
            analysis_manager.save_analysis(snapshot)

            base_log = (
                f"[DEBUG] side={setup['side']} conf={confidence:.2f} "
                f"state={gamma_context['gamma_state']} regime={gamma_context['market_regime']} "
                f"rr={(trade_plan['risk_reward'] if trade_plan else 0):.2f} "
                f"gamma_gate={'DISABLED' if _gamma_gate_disabled() else 'ENABLED'} "
                f"reason={decision.get('reason', '')}"
            )

            if decision["status"] == "READY":
                print(base_log)
                last_wait_signature = ""
            else:
                wait_signature = "|".join(
                    (
                        decision.get("reason", ""),
                        gamma_context.get("gamma_state", "UNKNOWN"),
                        gamma_context.get("market_regime", "UNKNOWN"),
                        f"{confidence:.2f}",
                        setup.get("side", ""),
                    )
                )
                if wait_signature != last_wait_signature:
                    print(base_log)
                    last_wait_signature = wait_signature

            if decision["status"] != "READY":
                time.sleep(POLL_SECONDS)
                continue

            # --- EXECUTE THE ENTRY TRADE ---
            position_size_coin = float(TP.get("position_size", 0.01))
            signal_time_raw = latest_bar.get("datetime")
            signal_epoch = _safe_to_epoch(signal_time_raw)
            if signal_epoch is None:
                signal_epoch = time.time()
            signal_price = float(latest_bar.get("close", current_price))
            submit_started_epoch = time.time()

            entry_result = execute_trade(
                action=setup["side"],
                symbol=SYMBOL,
                price=trade_plan["entry_price"],
                sz=position_size_coin,
            )
            submit_completed_epoch = time.time()
            if entry_result is None:
                print(
                    "[WARN] Entry setup was READY but broker execution failed. "
                    "Skipping state/signal update for this attempt.",
                    flush=True,
                )
                time.sleep(POLL_SECONDS)
                continue

            exec_meta = _extract_execution_meta(entry_result)
            submitted_epoch = (
                _safe_to_epoch(exec_meta.get("submitted_at")) or submit_started_epoch
            )
            filled_epoch = _safe_to_epoch(exec_meta.get("filled_at"))
            fill_price = _safe_to_float(exec_meta.get("filled_avg_price"))
            if fill_price is None:
                fill_price = _safe_to_float(exec_meta.get("limit_price"))
            if fill_price is None:
                fill_price = float(trade_plan["entry_price"])

            signal_to_submit_ms = int((submitted_epoch - signal_epoch) * 1000)
            request_rtt_ms = int((submit_completed_epoch - submit_started_epoch) * 1000)
            signal_to_fill_ms = (
                int((filled_epoch - signal_epoch) * 1000) if filled_epoch else None
            )

            direction = 1.0 if setup["side"] == "BUY" else -1.0
            signal_to_fill_signed = (fill_price - signal_price) * direction
            signal_to_fill_abs = abs(fill_price - signal_price)
            plan_to_fill_signed = (fill_price - float(trade_plan["entry_price"])) * direction
            plan_to_fill_bps = (
                (plan_to_fill_signed / float(trade_plan["entry_price"])) * 10000
                if float(trade_plan["entry_price"]) > 0
                else 0.0
            )

            print(
                "[SLIPPAGE] "
                f"side={setup['side']} "
                f"signal_time={signal_time_raw} "
                f"signal_to_submit_ms={signal_to_submit_ms} "
                f"submit_rtt_ms={request_rtt_ms} "
                f"signal_to_fill_ms={signal_to_fill_ms if signal_to_fill_ms is not None else 'NA'} "
                f"signal_px={signal_price:.2f} "
                f"plan_px={float(trade_plan['entry_price']):.2f} "
                f"fill_px={fill_price:.2f} "
                f"signal_to_fill_usd={signal_to_fill_signed:.2f} "
                f"signal_to_fill_abs_usd={signal_to_fill_abs:.2f} "
                f"plan_to_fill_usd={plan_to_fill_signed:.2f} "
                f"plan_to_fill_bps={plan_to_fill_bps:.2f} "
                f"order_status={exec_meta.get('status', 'unknown')} "
                f"order_id={exec_meta.get('id', 'na')}",
                flush=True,
            )
            append_execution_telemetry(
                {
                    "logged_at_utc": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "broker": BROKER,
                    "symbol": SYMBOL,
                    "side": setup["side"],
                    "signal_time_raw": signal_time_raw,
                    "signal_epoch": signal_epoch,
                    "submitted_epoch": submitted_epoch,
                    "filled_epoch": filled_epoch,
                    "signal_to_submit_ms": signal_to_submit_ms,
                    "submit_rtt_ms": request_rtt_ms,
                    "signal_to_fill_ms": signal_to_fill_ms,
                    "signal_price": signal_price,
                    "plan_price": float(trade_plan["entry_price"]),
                    "fill_price": fill_price,
                    "signal_to_fill_usd_signed": signal_to_fill_signed,
                    "signal_to_fill_usd_abs": signal_to_fill_abs,
                    "plan_to_fill_usd_signed": plan_to_fill_signed,
                    "plan_to_fill_bps": plan_to_fill_bps,
                    "size": position_size_coin,
                    "order_id": exec_meta.get("id"),
                    "order_status": exec_meta.get("status"),
                }
            )
            if signal_to_fill_abs >= EXECUTION_SLIPPAGE_ALERT_USD:
                print(
                    "[SLIPPAGE][ALERT] "
                    f"abs_spread_usd={signal_to_fill_abs:.2f} exceeds "
                    f"threshold={EXECUTION_SLIPPAGE_ALERT_USD:.2f}",
                    flush=True,
                )

            timestamp = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
            state["last_signal_epoch"] = time.time()
            state["daily_trades"] += 1
            state["open_position"] = {
                "side": setup["side"],
                "size": position_size_coin,  # Saved so we can close this exact amount later
                "entry_epoch": time.time(),
                "entry_price": trade_plan["entry_price"],
                "stop_loss": trade_plan["stop_loss"],
                "target_price": trade_plan["target_price"],
                "risk_points": trade_plan["risk_points"],
                "reward_points": trade_plan["reward_points"],
                "risk_reward": trade_plan["risk_reward"],
                "regime": gamma_context["market_regime"],
                "gamma_state": gamma_context["gamma_state"],
                "gamma_flip": gamma_context["gamma_flip"],
                "call_wall": gamma_context["major_call_wall"],
                "put_wall": gamma_context["major_put_wall"],
                "dist_to_flip": gamma_context["dist_to_flip"],
                "inside_walls": gamma_context["inside_walls"],
            }
            save_state(state)

            signal_writer.append_signal(
                timestamp=timestamp,
                direction="LONG" if setup["side"] == "BUY" else "SHORT",
                entry_price=trade_plan["entry_price"],
                stop_loss=trade_plan["stop_loss"],
                take_profit=trade_plan["target_price"],
            )

            print(
                f"[ENTRY] {setup['side']} | price={trade_plan['entry_price']:.2f} "
                f"stop={trade_plan['stop_loss']:.2f} "
                f"target={trade_plan['target_price']:.2f} "
                f"conf={confidence:.2f}"
            )

        except KeyboardInterrupt:
            print("[INFO] Engine stop requested. Exiting main loop.", flush=True)
            break
        except Exception as exc:
            print(f"[ERROR] ENGINE ERROR: {exc}", flush=True)
            time.sleep(5)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main_loop()
