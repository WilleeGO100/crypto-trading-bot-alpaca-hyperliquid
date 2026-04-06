import os
import time
import requests
import eth_account
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


def main():
    # 1. Load from .env and check master switch
    load_dotenv()
    env_mode = os.getenv("HL_ENVIRONMENT", "paper").lower()

    if env_mode == "live":
        secret_key = os.getenv("HL_LIVE_SECRET_KEY")
        address = os.getenv("HL_LIVE_ACCOUNT_ADDRESS")
        url = "https://api.hyperliquid.xyz/info"
        api_url = constants.MAINNET_API_URL
    else:
        secret_key = os.getenv("HL_PAPER_SECRET_KEY")
        address = os.getenv("HL_PAPER_ACCOUNT_ADDRESS")
        url = "https://api.hyperliquid-testnet.xyz/info"
        api_url = constants.TESTNET_API_URL

    if not secret_key or not address:
        print(f"[ERROR] Error: Missing credentials for {env_mode.upper()} mode in the .env file.")
        return

    account = eth_account.Account.from_key(secret_key)
    exchange = Exchange(account, api_url, account_address=address)

    last_known_positions = {}
    print(f"[MANAGER] Smyrna Station Manager ({env_mode.upper()}): ONLINE. Monitoring all coins...")

    while True:
        try:
            ch_payload = {"type": "clearinghouseState", "user": address}
            res = requests.post(url, json=ch_payload).json()
            positions = res.get('assetPositions', [])

            for p in positions:
                coin = p['position']['coin']
                size = float(p['position']['szi'])
                entry = float(p['position']['entryPx'])

                # If size changed (Trap hit or position closed)
                if size != last_known_positions.get(coin, 0.0):
                    print(f"[EVENT] Activity in {coin}! New Size: {size} | Entry: {entry}")

                    # 1. Cancel old TPs for this coin
                    o_payload = {"type": "openOrders", "user": address}
                    open_orders = requests.post(url, json=o_payload).json()
                    for o in open_orders:
                        if o.get('coin') == coin:
                            exchange.cancel(coin, o["oid"])

                    # 2. Set new Unified TP
                    if size != 0:
                        is_buy = size < 0  # To close long, sell (False). To close short, buy (True).
                        tp_px = int(entry * 1.005) if size > 0 else int(entry * 0.995)
                        exchange.order(coin, is_buy, abs(size), tp_px, {"limit": {"tif": "Gtc"}})
                        print(f"[TP] {coin} TP set at ${tp_px}")

                    last_known_positions[coin] = size

        except Exception as e:
            print(f"[WARN] Manager Warning: {e}")

        time.sleep(10)


if __name__ == "__main__":
    main()