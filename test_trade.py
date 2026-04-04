import os
import eth_account
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


def main():
    # 1. Load the environment variables
    load_dotenv()

    # 2. Check the Master Switch
    env_mode = os.getenv("HL_ENVIRONMENT", "paper").lower() # Defaults to paper for safety

    # 3. Route to the correct variables based on the switch
    if env_mode == "live":
        print("🟢 WARNING: RUNNING IN LIVE MAINNET MODE")
        secret_key = os.getenv("HL_LIVE_SECRET_KEY")
        account_address = os.getenv("HL_LIVE_ACCOUNT_ADDRESS")
        api_url = constants.MAINNET_API_URL
    else:
        print("🟡 RUNNING IN PAPER TESTNET MODE")
        secret_key = os.getenv("HL_PAPER_SECRET_KEY")
        account_address = os.getenv("HL_PAPER_ACCOUNT_ADDRESS")
        api_url = constants.TESTNET_API_URL

    # 4. Safety Check
    if not secret_key or not account_address:
        print(f"❌ Error: Missing credentials for {env_mode.upper()} mode in the .env file.")
        return

    # 5. Setup the API Wallet
    account = eth_account.Account.from_key(secret_key)
    print(f"Connecting Agent to {env_mode.capitalize()} Wallet: {account_address[:8]}...")

    # 6. Initialize the Exchange (Automatically uses the right URL and Address)
    exchange = Exchange(account, api_url, account_address=account_address)

    try:
        # Example: Place a tiny 0.001 BTC Long order
        print("Sending 0.001 BTC Long order...")
        result = exchange.market_open("BTC", True, 0.001, None, 0.01)

        if result["status"] == "ok":
            print(f"✅ SUCCESS! Trade is LIVE on {env_mode.capitalize()}.")
        else:
            print(f"❌ Order Failed: {result}")

    except Exception as e:
        print(f"❌ System Error: {e}")


if __name__ == "__main__":
    main()