import json
import requests


def main():
    with open("config.json", "r") as f:
        config = json.load(f)

    address = config["account_address"]
    # We are checking the TESTNET here to see the $1,000
    url = "https://api.hyperliquid-testnet.xyz/info"

    print(f"Checking TESTNET for: {address}")

    payload = {"type": "clearinghouseState", "user": address}
    res = requests.post(url, json=payload).json()

    # This pulls the total value including the 'fake' 1,000 USDC
    balance = res.get("marginSummary", {}).get("accountValue", "0.0")

    print("\n--- Testnet Status ---")
    print(f"Total Value: ${balance}")
    print("----------------------")

    if float(balance) == 0.0:
        print("[ERROR] Still $0. Go to the 'Drip' page and hit 'Claim' again.")
    else:
        print("[OK] SUCCESS! The $1,000 is live. Ready to trade.")


if __name__ == "__main__":
    main()