import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Tuple

import eth_account
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from hyperliquid_signal_executor import HyperliquidSignalExecutor, IdempotencyStore, risk_config_from_env


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_env_mode() -> str:
    raw_mode = os.getenv("HL_ENVIRONMENT", "").strip().lower()
    if raw_mode in {"live", "mainnet"}:
        return "live"
    if raw_mode in {"paper", "testnet"}:
        return "paper"
    return "paper" if _env_bool("USE_TESTNET", default=True) else "live"


def _first_eth_address(*keys: str) -> Tuple[str, str]:
    for key in keys:
        val = os.getenv(key, "").strip()
        if val.startswith("0x"):
            return val, key
    return "", ""


def _pick_credentials() -> Tuple[str, str, str, str]:
    env_mode = _resolve_env_mode()
    if env_mode == "live":
        secret_key = os.getenv("HL_SECRET_KEY", "").strip() or os.getenv("HL_LIVE_SECRET_KEY", "").strip()
        account_address, _ = _first_eth_address(
            "HL_INFO_USER_ADDRESS",
            "HL_MAIN_ACCOUNT_ADDRESS",
            "HL_ACCOUNT_ADDRESS",
            "HL_LIVE_ACCOUNT_ADDRESS",
            "HL_WALLET_ADDRESS",
        )
        api_url = os.getenv("HL_MAINNET_API_URL", os.getenv("HL_LIVE_API_URL", constants.MAINNET_API_URL))
    else:
        secret_key = os.getenv("HL_TESTNET_SECRET_KEY", "").strip() or os.getenv("HL_PAPER_SECRET_KEY", "").strip()
        account_address, _ = _first_eth_address(
            "HL_TESTNET_INFO_USER_ADDRESS",
            "HL_PAPER_INFO_USER_ADDRESS",
            "HL_TESTNET_MAIN_ACCOUNT_ADDRESS",
            "HL_TESTNET_ACCOUNT_ADDRESS",
            "HL_PAPER_ACCOUNT_ADDRESS",
            "HL_TESTNET_WALLET_ADDRESS",
            "HL_WALLET_ADDRESS",
        )
        api_url = os.getenv("HL_TESTNET_API_URL", os.getenv("HL_PAPER_API_URL", constants.TESTNET_API_URL))

    if secret_key and not account_address:
        account_address = eth_account.Account.from_key(secret_key).address
    return env_mode, secret_key, account_address, api_url


class TradingViewWebhookHandler(BaseHTTPRequestHandler):
    executor: HyperliquidSignalExecutor = None
    webhook_secret: str = ""

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True})
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/webhook":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload_must_be_json_object")
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid_json"})
            return

        header_secret = self.headers.get("X-Webhook-Secret", "")
        body_secret = str(payload.get("secret", "")).strip()
        if self.webhook_secret and header_secret != self.webhook_secret and body_secret != self.webhook_secret:
            self._send_json(401, {"ok": False, "error": "invalid_secret"})
            return

        try:
            result = self.executor.execute(payload)
            self._send_json(200, result)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "execution_failed", "detail": str(exc)})


def main() -> None:
    load_dotenv()

    env_mode, secret_key, account_address, api_url = _pick_credentials()
    if not secret_key or not account_address:
        print("[ERROR] Missing Hyperliquid credentials in .env.")
        return

    webhook_secret = os.getenv("TV_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        print("[ERROR] Set TV_WEBHOOK_SECRET in .env before starting this server.")
        return

    host = os.getenv("TV_WEBHOOK_HOST", "0.0.0.0").strip()
    port = int(float(os.getenv("TV_WEBHOOK_PORT", "8000")))
    default_coin = os.getenv("TRADE_SYMBOL", "BTC").strip().upper()

    account = eth_account.Account.from_key(secret_key)
    exchange = Exchange(account, api_url, account_address=account_address)

    id_file = os.getenv("TV_IDEMPOTENCY_FILE", "data/tv_webhook_seen_ids.json").strip()
    id_ttl = int(float(os.getenv("TV_IDEMPOTENCY_TTL_SECONDS", str(6 * 3600))))
    id_store = IdempotencyStore(file_path=id_file, ttl_seconds=id_ttl)
    risk = risk_config_from_env()

    TradingViewWebhookHandler.executor = HyperliquidSignalExecutor(
        exchange=exchange,
        account_address=account_address,
        risk=risk,
        default_coin=default_coin,
        id_store=id_store,
    )
    TradingViewWebhookHandler.webhook_secret = webhook_secret

    server = ThreadingHTTPServer((host, port), TradingViewWebhookHandler)
    print(f"[WEBHOOK] TradingView bridge online in {env_mode.upper()} mode at http://{host}:{port}")
    print("[WEBHOOK] POST /webhook with JSON payload, secret via header `X-Webhook-Secret` or payload `secret`.")
    print("[WEBHOOK] Health check at GET /health")
    print(f"[WEBHOOK] Risk defaults: slippage={risk.default_slippage}, allow_shorts={risk.allow_short}")
    server.serve_forever()


if __name__ == "__main__":
    main()
