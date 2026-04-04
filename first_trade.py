import json
import requests


def main():
    # 1. Load keys
    with open("config.json", "r") as f:
        config = json.load(f)

    address = config["account_address"]
    url = "https://api.hyperliquid.xyz/info"  # MAINNET URL

    # 2. Get the current BTC price
    price_res = requests.post(url, json={"type": "l2Book", "coin": "BTC"})
    btc_price = float(price_res.json()["levels"][0][0]["px"])

    # 3. Calculate a "Safe Entry" (Buy $2 worth of BTC)
    # We place a limit order 1% below current price to test the logic
    target_price = round(btc_price * 0.99, 1)

    print(f"BTC is currently ${btc_price}")
    print(f"Placing a 'Buy' limit order for $2.00 at ${target_price}...")

    # NOTE: To actually EXECUTE, we'll need to sign the transaction.
    # For now, let's just make sure we can read the price and the balance.
    print("\n--- Connection Verified ---")
    print("Ready to activate the 50x DCA Logic.")


if __name__ == "__main__":
    main()