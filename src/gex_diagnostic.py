import os
import requests
import json
from dotenv import load_dotenv


def gex_probe():
    load_dotenv()
    api_key = os.getenv("GEXBOT_API_KEY")

    # We'll try the common SPX path
    url = f"https://api.gexbot.com/SPX/classic/zero"
    params = {"key": api_key}

    print(f"📡 Probing GEXBot at: {url}")

    try:
        response = requests.get(url, params=params)
        print(f"🔌 Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            # This prints EVERYTHING GEXBot is sending us
            print("\n📦 RAW DATA RECEIVED:")
            print(json.dumps(data, indent=4))

            # Check for the key we need
            if 'zerogamma' in data:
                print(f"\n✅ FOUND 'zerogamma': {data['zerogamma']}")
            elif 'zero_gamma' in data:
                print(f"\n✅ FOUND 'zero_gamma': {data['zero_gamma']}")
            else:
                print("\n❌ Key 'zerogamma' not found. Look at the raw data above for the correct label.")
        else:
            print(f"❌ Error: {response.text}")

    except Exception as e:
        print(f"❌ Probe failed: {e}")


if __name__ == "__main__":
    gex_probe()