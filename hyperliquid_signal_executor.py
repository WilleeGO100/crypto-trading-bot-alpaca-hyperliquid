import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from hyperliquid.exchange import Exchange


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class RiskConfig:
    default_slippage: float = 0.01
    default_order_usd: float = 0.0
    max_usd_per_order: float = 0.0
    max_coin_per_order: float = 0.0
    cooldown_seconds: int = 0
    allow_short: bool = True


class IdempotencyStore:
    def __init__(self, file_path: str, ttl_seconds: int = 6 * 3600, max_items: int = 5000) -> None:
        self.file_path = Path(file_path)
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._lock = threading.Lock()
        self._data: Dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.file_path.exists():
                raw = json.loads(self.file_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    now = time.time()
                    for key, ts in raw.items():
                        tsf = _to_float(ts, 0.0)
                        if tsf > 0 and (now - tsf) <= self.ttl_seconds:
                            self._data[str(key)] = tsf
        except Exception:
            self._data = {}

    def _persist(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(self._data), encoding="utf-8")

    def check_and_add(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            for k, ts in list(self._data.items()):
                if (now - ts) > self.ttl_seconds:
                    self._data.pop(k, None)

            if key in self._data:
                return False

            self._data[key] = now
            if len(self._data) > self.max_items:
                ordered = sorted(self._data.items(), key=lambda kv: kv[1], reverse=True)
                self._data = dict(ordered[: self.max_items])

            self._persist()
            return True


class HyperliquidSignalExecutor:
    def __init__(
        self,
        exchange: Exchange,
        account_address: str,
        risk: RiskConfig,
        default_coin: str = "BTC",
        id_store: Optional[IdempotencyStore] = None,
    ) -> None:
        self.exchange = exchange
        self.account_address = account_address
        self.risk = risk
        self.default_coin = default_coin.upper()
        self.id_store = id_store
        self._cooldown_by_coin: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _coin_price(self, coin: str) -> float:
        mids = self.exchange.info.all_mids()
        key = coin.upper()
        if key not in mids:
            raise ValueError(f"coin_not_found_in_mids: {key}")
        return float(mids[key])

    def _normalize_signal(self, payload: Dict[str, Any]) -> str:
        signal = str(payload.get("signal", "")).strip().lower()
        if signal in {"long_entry", "entry_long", "open_long", "go_long"}:
            return "open_long"
        if signal in {"short_entry", "entry_short", "open_short", "go_short"}:
            return "open_short"
        if signal in {"long_exit", "exit_long", "close_long"}:
            return "close"
        if signal in {"short_exit", "exit_short", "close_short"}:
            return "close"
        if signal in {"flat", "close_all", "close"}:
            return "close"

        action = str(payload.get("action", "")).strip().lower()
        side = str(payload.get("side", "")).strip().lower()
        reduce_only = _to_bool(payload.get("reduce_only"), default=False) or _to_bool(payload.get("close"), default=False)

        if action == "close":
            return "close"
        if action == "open":
            if side == "buy":
                return "open_long"
            if side == "sell":
                return "open_short"
        if action in {"buy", "long"}:
            return "close" if reduce_only else "open_long"
        if action in {"sell", "short"}:
            return "close" if reduce_only else "open_short"
        raise ValueError("unsupported_signal_or_action")

    def _extract_size(self, payload: Dict[str, Any], coin: str) -> Tuple[float, float]:
        size = _to_float(payload.get("size", 0.0), 0.0)
        usd_size = _to_float(payload.get("usd_size", payload.get("size_usd", payload.get("notional_usd", 0.0))), 0.0)

        if size <= 0 and usd_size <= 0 and self.risk.default_order_usd > 0:
            usd_size = self.risk.default_order_usd

        if size <= 0 and usd_size > 0:
            px = self._coin_price(coin)
            size = usd_size / px

        if size <= 0:
            raise ValueError("size_or_usd_size_required")

        if self.risk.max_coin_per_order > 0 and size > self.risk.max_coin_per_order:
            raise ValueError("max_coin_per_order_exceeded")

        if usd_size <= 0:
            usd_size = size * self._coin_price(coin)
        if self.risk.max_usd_per_order > 0 and usd_size > self.risk.max_usd_per_order:
            raise ValueError("max_usd_per_order_exceeded")

        return size, usd_size

    def _enforce_cooldown(self, coin: str) -> None:
        if self.risk.cooldown_seconds <= 0:
            return
        now = time.time()
        with self._lock:
            prev = self._cooldown_by_coin.get(coin, 0.0)
            if now - prev < self.risk.cooldown_seconds:
                raise ValueError("cooldown_active")
            self._cooldown_by_coin[coin] = now

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        coin = str(payload.get("symbol", payload.get("coin", self.default_coin))).strip().upper()
        signal = self._normalize_signal(payload)

        slippage = _to_float(payload.get("slippage", self.risk.default_slippage), self.risk.default_slippage)
        if slippage <= 0:
            slippage = self.risk.default_slippage

        alert_id = str(payload.get("alert_id", payload.get("id", payload.get("uuid", "")))).strip()
        if alert_id and self.id_store and not self.id_store.check_and_add(alert_id):
            return {"ok": True, "duplicate": True, "alert_id": alert_id, "symbol": coin}

        if signal == "open_short" and not self.risk.allow_short:
            raise ValueError("shorts_disabled_by_risk_config")

        self._enforce_cooldown(coin)

        if signal == "close":
            result = self.exchange.market_close(coin, None, None, slippage)
            return {"ok": True, "signal": signal, "symbol": coin, "slippage": slippage, "result": result}

        size, usd_size = self._extract_size(payload, coin)
        is_buy = signal == "open_long"
        result = self.exchange.market_open(coin, is_buy, size, None, slippage)
        return {
            "ok": True,
            "signal": signal,
            "symbol": coin,
            "size": size,
            "usd_size": usd_size,
            "slippage": slippage,
            "result": result,
        }


def risk_config_from_env() -> RiskConfig:
    return RiskConfig(
        default_slippage=_to_float(os.getenv("TV_DEFAULT_SLIPPAGE", "0.01"), 0.01),
        default_order_usd=_to_float(os.getenv("TV_DEFAULT_ORDER_USD", "0"), 0.0),
        max_usd_per_order=_to_float(os.getenv("TV_MAX_USD_PER_ORDER", "0"), 0.0),
        max_coin_per_order=_to_float(os.getenv("TV_MAX_COIN_PER_ORDER", "0"), 0.0),
        cooldown_seconds=int(_to_float(os.getenv("TV_COOLDOWN_SECONDS", "0"), 0)),
        allow_short=_to_bool(os.getenv("TV_ALLOW_SHORTS", "true"), default=True),
    )
