import os
import time
import requests
import eth_account
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


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


def _first_eth_address(*keys: str):
    for key in keys:
        val = os.getenv(key, "").strip()
        if val.startswith("0x"):
            return val, key
    return "", ""


def _post_json(url: str, payload: dict):
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _px_round(px: float) -> float:
    if px >= 1000:
        return round(px, 1)
    if px >= 100:
        return round(px, 2)
    if px >= 1:
        return round(px, 3)
    return round(px, 5)


def main():
    load_dotenv()
    env_mode = _resolve_env_mode()

    if env_mode == "live":
        secret_key = os.getenv("HL_SECRET_KEY", "").strip() or os.getenv("HL_LIVE_SECRET_KEY", "").strip()
        info_user, info_key = _first_eth_address(
            "HL_INFO_USER_ADDRESS",
            "HL_MAIN_ACCOUNT_ADDRESS",
            "HL_ACCOUNT_ADDRESS",
            "HL_LIVE_ACCOUNT_ADDRESS",
            "HL_WALLET_ADDRESS",
        )
        info_url = os.getenv("HL_MAINNET_INFO_URL", os.getenv("HL_LIVE_INFO_URL", os.getenv("HL_INFO_URL", "https://api.hyperliquid.xyz/info")))
        api_url = os.getenv("HL_MAINNET_API_URL", os.getenv("HL_LIVE_API_URL", constants.MAINNET_API_URL))
    else:
        secret_key = os.getenv("HL_TESTNET_SECRET_KEY", "").strip() or os.getenv("HL_PAPER_SECRET_KEY", "").strip()
        info_user, info_key = _first_eth_address(
            "HL_TESTNET_INFO_USER_ADDRESS",
            "HL_PAPER_INFO_USER_ADDRESS",
            "HL_TESTNET_MAIN_ACCOUNT_ADDRESS",
            "HL_TESTNET_ACCOUNT_ADDRESS",
            "HL_PAPER_ACCOUNT_ADDRESS",
            "HL_TESTNET_WALLET_ADDRESS",
            "HL_WALLET_ADDRESS",
        )
        info_url = os.getenv("HL_TESTNET_INFO_URL", os.getenv("HL_PAPER_INFO_URL", "https://api.hyperliquid-testnet.xyz/info"))
        api_url = os.getenv("HL_TESTNET_API_URL", os.getenv("HL_PAPER_API_URL", constants.TESTNET_API_URL))

    signer_address = ""
    if secret_key:
        signer_address = eth_account.Account.from_key(secret_key).address
    if not info_user and signer_address:
        info_user = signer_address
        info_key = "fallback_to_signer_address"

    if not secret_key or not info_user:
        print("[ERROR] Missing secret key or info user address.")
        return

    account = eth_account.Account.from_key(secret_key)
    exchange = Exchange(account, api_url, account_address=info_user)

    tp_profit_pct = float(os.getenv("TCL_MANAGER_TP_PCT", "0.005"))
    poll_sec = int(float(os.getenv("TCL_MANAGER_POLL_SEC", "10")))

    last_known_positions = {}
    print(f"[MANAGER] ONLINE ({env_mode.upper()})")
    print(f"[CFG] Signer wallet: {signer_address}")
    print(f"[CFG] Info user: {info_user} (source: {info_key or 'unknown'})")
    print(f"[CFG] TP pct: {tp_profit_pct:.4f} | Poll: {poll_sec}s")

    while True:
        try:
            ch_state = _post_json(info_url, {"type": "clearinghouseState", "user": info_user})
            positions = ch_state.get("assetPositions", [])

            active = {}
            for row in positions:
                p = row.get("position", {})
                coin = str(p.get("coin", "")).split(":")[-1].upper()
                size = float(p.get("szi", 0))
                entry = float(p.get("entryPx", 0))
                if not coin:
                    continue
                active[coin] = size

                if size == 0:
                    continue
                if size == last_known_positions.get(coin):
                    continue

                print(f"[EVENT] {coin} size changed -> {size} @ entry {entry}")

                # Cancel only reduce-only orders for this coin (preserve entry ladders).
                f_orders = _post_json(info_url, {"type": "frontendOpenOrders", "user": info_user})
                for o in f_orders:
                    if str(o.get("coin", "")).upper() == coin and bool(o.get("reduceOnly")):
                        oid = o.get("oid")
                        if oid is not None:
                            exchange.cancel(coin, int(oid))

                # Place fresh unified reduce-only TP at current avg entry.
                is_buy_to_close = size < 0
                tp_px = _px_round(entry * (1.0 + tp_profit_pct) if size > 0 else entry * (1.0 - tp_profit_pct))
                exchange.order(coin, is_buy_to_close, abs(size), tp_px, {"limit": {"tif": "Gtc"}}, reduce_only=True)
                print(f"[TP] {coin} reduce-only TP set at {tp_px}")

                last_known_positions[coin] = size

            # Clean up cache when position fully closed.
            for coin in list(last_known_positions.keys()):
                if active.get(coin, 0.0) == 0.0:
                    last_known_positions.pop(coin, None)

        except Exception as e:
            print(f"[WARN] Manager warning: {e}")

        time.sleep(poll_sec)


if __name__ == "__main__":
    main()
