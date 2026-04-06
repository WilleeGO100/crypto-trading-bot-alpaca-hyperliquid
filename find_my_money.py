import json
import requests


def main():
    with open("config.json", "r") as f:
        config = json.load(f)

    address = config["account_address"]

    # 1. Check Real Money (Mainnet)
    main_url = "https://api.hyperliquid.xyz/info"
    main_res = requests.post(main_url, json={"type": "clearinghouseState", "user": address}).json()
    real_bal = main_res.get("marginSummary", {}).get("accountValue", "0.0")

    # 2. Check Fake Money (Testnet)
    test_url = "https://api.hyperliquid-testnet.xyz/info"
    test_res = requests.post(test_url, json={"type": "clearinghouseState", "user": address}).json()
    fake_bal = test_res.get("marginSummary", {}).get("accountValue", "0.0")

    print("\n--- The Money Map ---")
    print(f"Real Account (Mainnet): ${real_bal}")
    print(f"Fake Account (Testnet): ${fake_bal}")
    print("----------------------")

    if float(real_bal) > 0:
        print("[OK] Found it! Your money is on the REAL exchange.")
    elif float(fake_bal) > 0:
        print("[OK] Found it! Your money is on the TEST exchange.")
    else:
        print("[ERROR] Both are $0. It might still be in your 'Spot' wallet (not yet in the Trading account).")


if __name__ == "__main__":
    main()