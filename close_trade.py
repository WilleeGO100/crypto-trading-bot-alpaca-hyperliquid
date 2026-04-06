import os
import eth_account
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


def main():
    # 1. Load the environment variables
    load_dotenv()

    # 2. Check the Master Switch (defaults to paper for safety)
    env_mode = os.getenv("HL_ENVIRONMENT", "paper").lower()

    # 3. Route to the correct variables based on the switch
    if env_mode == "live":
        print("[LIVE] WARNING: RUNNING IN LIVE MAINNET MODE")
        secret_key = os.getenv("HL_LIVE_SECRET_KEY")
        account_address = os.getenv("HL_LIVE_ACCOUNT_ADDRESS")
        api_url = constants.MAINNET_API_URL
    else:
        print("[PAPER] RUNNING IN PAPER TESTNET MODE")
        secret_key = os.getenv("HL_PAPER_SECRET_KEY")
        account_address = os.getenv("HL_PAPER_ACCOUNT_ADDRESS")
        api_url = constants.TESTNET_API_URL

    # 4. Pull the symbol from your .env (Defaults to "BTC" as a safety fallback)
    trade_symbol = os.getenv("TRADE_SYMBOL", "BTC")

    # 5. Safety Check
    if not secret_key or not account_address:
        print(f"[ERROR] Error: Missing credentials for {env_mode.upper()} mode in the .env file.")
        return

    # 6. Setup the API Wallet
    account = eth_account.Account.from_key(secret_key)

    # 7. Setup the Exchange (Automatically handles Mainnet vs Testnet)
    exchange = Exchange(account, api_url, account_address=account_address)

    print(f"--- [ALERT] {env_mode.upper()}: EMERGENCY CLOSE ---")

    try:
        # 8. Close the position using your dynamic symbol
        print(f"Closing all {trade_symbol} positions...")
        result = exchange.market_close(trade_symbol)

        if result["status"] == "ok":
            print(f"[OK] SUCCESS! {trade_symbol} position closed. You are back to cash.")
        else:
            print(f"[ERROR] Close Failed: {result}")

    except Exception as e:
        print(f"[ERROR] System Error: {e}")


if __name__ == "__main__":
    main()