"""
BTC On-Chain Snapshot Adapter

Scaffold module to normalize BTC on-chain metrics from multiple providers.
Designed to merge with deribit_gamma.get_btc_gamma_snapshot() output.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from env_profiles import load_env_profile


load_env_profile("engine")

SESSION = requests.Session()
REQUEST_TIMEOUT = float(os.getenv("ONCHAIN_HTTP_TIMEOUT", "8"))

ONCHAIN_PROVIDER = os.getenv("ONCHAIN_PROVIDER", "glassnode").strip().lower()
GLASSNODE_API_KEY = os.getenv("GLASSNODE_API_KEY", "").strip()
COINMETRICS_API_KEY = os.getenv("COINMETRICS_API_KEY", "").strip()
CRYPTOQUANT_API_KEY = os.getenv("CRYPTOQUANT_API_KEY", "").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        out = float(value)
        if out != out:  # NaN guard
            return None
        return out
    except (TypeError, ValueError):
        return None


def _default_snapshot(provider: str, status: str = "UNAVAILABLE", error: str = "") -> Dict[str, Any]:
    return {
        "onchain_provider": provider,
        "onchain_status": status,
        "onchain_error": error or None,
        "onchain_timestamp_utc": _now_iso(),
        "btc_active_addresses": None,
        "btc_exchange_netflow": None,
        "btc_miner_netflow": None,
        "btc_fees_usd": None,
        "btc_mvrv": None,
        "btc_sopr": None,
        "btc_nupl": None,
    }


def _extract_latest_value(payload: Any) -> Optional[float]:
    # Handles common provider payloads with either a list of rows or a single row.
    if isinstance(payload, list):
        if not payload:
            return None
        last = payload[-1]
        if isinstance(last, dict):
            for key in ("v", "value", "result", "metric"):
                if key in last:
                    val = _safe_float(last.get(key))
                    if val is not None:
                        return val
            for val in last.values():
                parsed = _safe_float(val)
                if parsed is not None:
                    return parsed
    if isinstance(payload, dict):
        # CoinMetrics style
        data = payload.get("data")
        if isinstance(data, list) and data:
            last = data[-1]
            if isinstance(last, dict):
                for key, val in last.items():
                    if key in {"time", "asset"}:
                        continue
                    parsed = _safe_float(val)
                    if parsed is not None:
                        return parsed
        # Generic single-value payload
        for key in ("v", "value", "result"):
            if key in payload:
                parsed = _safe_float(payload.get(key))
                if parsed is not None:
                    return parsed
    return None


def _http_get_json(url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Any:
    response = SESSION.get(url, params=params or {}, headers=headers or {}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _fetch_glassnode() -> Dict[str, Any]:
    provider = "glassnode"
    if not GLASSNODE_API_KEY:
        return _default_snapshot(provider, status="MISSING_API_KEY", error="GLASSNODE_API_KEY not set")

    # Source: https://api.glassnode.com/v1/metrics/*
    # Default interval is daily; can be overridden with ONCHAIN_INTERVAL (ex: 24h, 10m).
    interval = os.getenv("ONCHAIN_INTERVAL", "24h").strip()
    base = "https://api.glassnode.com/v1/metrics"

    metric_urls = {
        "btc_active_addresses": f"{base}/addresses/active_count",
        "btc_exchange_netflow": f"{base}/distribution/exchange_net_position_change",
        "btc_miner_netflow": f"{base}/mining/miner_net_position_change",
        "btc_fees_usd": f"{base}/fees/volume_sum",
        "btc_mvrv": f"{base}/market/mvrv",
        "btc_sopr": f"{base}/indicators/sopr_adjusted",
        "btc_nupl": f"{base}/indicators/net_unrealized_profit_loss",
    }

    out = _default_snapshot(provider, status="OK")
    errors: list[str] = []

    for field, url in metric_urls.items():
        try:
            payload = _http_get_json(url, params={"a": "BTC", "api_key": GLASSNODE_API_KEY, "i": interval})
            out[field] = _extract_latest_value(payload)
        except Exception as exc:
            errors.append(f"{field}: {exc}")

    # Degrade status when nothing could be fetched.
    fetched_count = sum(1 for k in metric_urls if out.get(k) is not None)
    if fetched_count == 0:
        out["onchain_status"] = "ERROR"
    elif errors:
        out["onchain_status"] = "PARTIAL"

    if errors:
        out["onchain_error"] = "; ".join(errors[:3])

    return out


def _fetch_coinmetrics() -> Dict[str, Any]:
    provider = "coinmetrics"
    # Public endpoint supports some metrics without key. Key is optional.
    base = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

    metric_codes = {
        "btc_active_addresses": "AdrActCnt",
        "btc_fees_usd": "FeeTotUSD",
        "btc_mvrv": "CapMVRVCur",
        "btc_sopr": "SplyAct1d",  # proxy placeholder; replace with preferred SOPR metric if licensed
    }

    headers: Dict[str, str] = {}
    if COINMETRICS_API_KEY:
        headers["Authorization"] = f"Bearer {COINMETRICS_API_KEY}"

    out = _default_snapshot(provider, status="OK")
    errors: list[str] = []

    for field, metric_code in metric_codes.items():
        try:
            payload = _http_get_json(
                base,
                params={"assets": "btc", "metrics": metric_code, "page_size": 1},
                headers=headers,
            )
            out[field] = _extract_latest_value(payload)
        except Exception as exc:
            errors.append(f"{field}: {exc}")

    # CoinMetrics community data may not provide exchange/miner flow/NUPL directly.
    fetched_count = sum(1 for k in metric_codes if out.get(k) is not None)
    if fetched_count == 0:
        out["onchain_status"] = "ERROR"
    elif errors:
        out["onchain_status"] = "PARTIAL"

    if errors:
        out["onchain_error"] = "; ".join(errors[:3])

    return out


def _fetch_cryptoquant() -> Dict[str, Any]:
    provider = "cryptoquant"
    if not CRYPTOQUANT_API_KEY:
        return _default_snapshot(provider, status="MISSING_API_KEY", error="CRYPTOQUANT_API_KEY not set")

    # CryptoQuant endpoint structure varies by plan and metric.
    # Configure exact metric URLs in env vars for maximum flexibility.
    metric_env_urls = {
        "btc_active_addresses": "CRYPTOQUANT_BTC_ACTIVE_ADDRESSES_URL",
        "btc_exchange_netflow": "CRYPTOQUANT_BTC_EXCHANGE_NETFLOW_URL",
        "btc_miner_netflow": "CRYPTOQUANT_BTC_MINER_NETFLOW_URL",
        "btc_fees_usd": "CRYPTOQUANT_BTC_FEES_USD_URL",
        "btc_mvrv": "CRYPTOQUANT_BTC_MVRV_URL",
        "btc_sopr": "CRYPTOQUANT_BTC_SOPR_URL",
        "btc_nupl": "CRYPTOQUANT_BTC_NUPL_URL",
    }

    headers = {"Authorization": f"Bearer {CRYPTOQUANT_API_KEY}"}
    out = _default_snapshot(provider, status="OK")
    errors: list[str] = []

    for field, env_key in metric_env_urls.items():
        url = os.getenv(env_key, "").strip()
        if not url:
            continue
        try:
            payload = _http_get_json(url, headers=headers)
            out[field] = _extract_latest_value(payload)
        except Exception as exc:
            errors.append(f"{field}: {exc}")

    populated = sum(1 for field in metric_env_urls if out.get(field) is not None)
    if populated == 0:
        out["onchain_status"] = "ERROR"
        out["onchain_error"] = "No CryptoQuant metric URLs configured or all requests failed"
    elif errors:
        out["onchain_status"] = "PARTIAL"
        out["onchain_error"] = "; ".join(errors[:3])

    return out


def get_btc_onchain_snapshot(provider: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch a normalized BTC on-chain snapshot.

    Provider options:
    - glassnode (default)
    - coinmetrics
    - cryptoquant
    """
    selected = (provider or ONCHAIN_PROVIDER or "glassnode").strip().lower()

    try:
        if selected == "glassnode":
            return _fetch_glassnode()
        if selected == "coinmetrics":
            return _fetch_coinmetrics()
        if selected == "cryptoquant":
            return _fetch_cryptoquant()
        return _default_snapshot(selected, status="UNSUPPORTED_PROVIDER", error=f"Unsupported provider: {selected}")
    except Exception as exc:
        return _default_snapshot(selected, status="ERROR", error=str(exc))


if __name__ == "__main__":
    print("[INFO] Fetching BTC on-chain snapshot...")
    print(get_btc_onchain_snapshot())
