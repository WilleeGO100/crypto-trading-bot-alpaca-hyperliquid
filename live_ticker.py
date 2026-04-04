import time
import sys
from hyperliquid.info import Info
from hyperliquid.utils import constants


def main():
    # Initialize Info on Mainnet
    info = Info(constants.MAINNET_API_URL, skip_ws=False)

    print("--- 📡 LIVE MARKET FEED: BTC-USD ---")
    print("Keep this window active. Press Ctrl+C to stop.")

    def print_price(data):
        # We only care about the 'allMids' channel updates
        if data.get('channel') == 'allMids':
            mids = data.get('data', {}).get('mids', {})
            btc_price = mids.get('BTC')
            if btc_price:
                # \r overwrites the same line so your terminal stays clean
                sys.stdout.write(f"\r💰 BTC Current Price: ${btc_price}    ")
                sys.stdout.flush()

    try:
        # 1. Start the background subscription
        info.subscribe({"type": "allMids"}, print_price)

        # 2. THE ANCHOR: This keeps the script alive to catch your Ctrl+C
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n✅ Shutdown Signal Received.")
        print("Closing connection and exiting...")
        # The script will now naturally exit here
    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")


if __name__ == "__main__":
    main()