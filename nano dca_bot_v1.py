import json
import requests
import time


def main():
    with open("config.json", "r") as f:
        config = json.load(f)

    address = config["account_address"]
    # We are using the TESTNET URL to practice first
    url = "https://api.hyperliquid-testnet.xyz/info"

    print("--- [BOT] DCA Bot: Phase 1 (Scanning Market) ---")

    # 1. Get Live BTC Price
    res = requests.post(url, json={"type": "l2Book", "coin": "BTC"})
    price = float(res.json()["levels"][0][0]["px"])

    # 2. Logic: Entry and Target
    # We buy at current price, and set a "Take Profit" 0.5% higher
    tp_price = round(price * 1.005, 1)

    print(f"BTC Price: ${price}")
    print(f"Strategy: Entry at ${price} | Take Profit at ${tp_price}")

    # 3. Check Account Health (The Smart Scan)
    # Check Perp/Unified state
    payload = {"type": "clearinghouseState", "user": address}
    margin = requests.post(url, json=payload).json().get("marginSummary", {})
    perp_val = float(margin.get('accountValue', '0.0'))

    # Check Spot state
    spot_payload = {"type": "spotClearinghouseState", "user": address}
    spot_data = requests.post(url, json=spot_payload).json()
    spot_val = float(spot_data.get('balances', [{}])[0].get('total', '0.0'))

    # Use whichever one has the money
    available_funds = perp_val if perp_val > 0 else spot_val

    print(f"--- Balance Report ---")
    print(f"Perp Value: ${perp_val}")
    print(f"Spot Value: ${spot_val}")
    print(f"[PRICE] Trading Power: ${available_funds}")
    print("-----------------------")


if __name__ == "__main__":
    main()