import os
import requests
import time
from dotenv import load_dotenv

def main():
    # 1. Load from .env and check master switch
    load_dotenv()
    env_mode = os.getenv("HL_ENVIRONMENT", "paper").lower()

    if env_mode == "live":
        address = os.getenv("HL_LIVE_ACCOUNT_ADDRESS")
        url = "https://api.hyperliquid.xyz/info"
    else:
        address = os.getenv("HL_PAPER_ACCOUNT_ADDRESS")
        url = "https://api.hyperliquid-testnet.xyz/info"

    if not address:
        print(f"❌ Error: Missing address for {env_mode.upper()} mode in the .env file.")
        return

    # Pull target coin from .env, default to BTC if not set
    trade_symbol = os.getenv("TRADE_SYMBOL", "BTC")

    print(f"--- DCA Bot Activated ({env_mode.upper()} MODE) ---")

    # 1. Get Live Price
    res = requests.post(url, json={"type": "l2Book", "coin": trade_symbol})
    price = float(res.json()["levels"][0][0]["px"])
    print(f"Current {trade_symbol} Price: ${price}")

    # 2. Logic: Buy if price is stable, Exit at +1%
    tp_price = round(price * 1.01, 1)  # 1% Take Profit
    print(f"DCA Strategy: Buy at ${price} | Target Exit: ${tp_price}")

    # 3. Connection Check
    payload = {"type": "clearinghouseState", "user": address}
    margin = requests.post(url, json=payload).json().get("marginSummary", {})

    print(f"Funds Available: ${margin.get('withdrawable', '0.0')}")
    print("-------------------------")
    print("Bot is standing by to execute the first 50x Long.")


if __name__ == "__main__":
    main()