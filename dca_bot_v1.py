import os
import time
import requests
import eth_account
import pandas as pd
import math
import traceback
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


def get_sz_decimals(coin, spot_meta):
    if 'universe' in spot_meta:
        for asset in spot_meta['universe']:
            if asset['name'] == coin: return asset['szDecimals']
    return 5 if coin in ['BTC', 'ETH'] else 2


def main():
    # 1. Load from .env and check master switch
    load_dotenv()
    env_mode = os.getenv("HL_ENVIRONMENT", "paper").lower()

    if env_mode == "live":
        secret_key = os.getenv("HL_SECRET_KEY")
        address = os.getenv("HL_ACCOUNT_ADDRESS")
        url = "https://api.hyperliquid.xyz/info"
        api_url = constants.MAINNET_API_URL
    else:
        secret_key = os.getenv("HL_TESTNET_SECRET_KEY")
        address = os.getenv("HL_PAPER_ACCOUNT_ADDRESS")
        url = "https://api.hyperliquid-testnet.xyz/info"
        api_url = constants.TESTNET_API_URL

    if not secret_key or not address:
        print(f"❌ Error: Missing credentials for {env_mode.upper()} mode in the .env file.")
        return

    WATCHLIST = ['BTC', 'ETH', 'SOL', 'ARB', 'TIA']
    BASE_DOLLAR_SIZE = 15.0
    MAX_OPEN_TRADES = 3

    print(f"--- 🤖 Smyrna Station: Fibonacci Scanner ({env_mode.upper()}) ---")

    try:
        spot_meta = requests.post(url, json={"type": "spotMeta"}).json()
        ch_payload = {"type": "clearinghouseState", "user": address}
        ch_state = requests.post(url, json=ch_payload).json()
        active_pos = [p for p in ch_state.get('assetPositions', []) if float(p['position']['szi']) != 0]

        if len(active_pos) >= MAX_OPEN_TRADES:
            print(f"🛑 Position Limit ({MAX_OPEN_TRADES}) Reached.")
            return
    except Exception as e:
        print(f"⚠️ Sync Error: {e}")
        return

    for coin in WATCHLIST:
        print(f"\n🔍 Analyzing {coin}...")
        try:
            # A. Fetch Data
            now = int(time.time() * 1000)
            start = now - (200 * 5 * 60 * 1000)
            payload = {"type": "candleSnapshot",
                       "req": {"coin": coin, "interval": "5m", "startTime": start, "endTime": now}}
            res = requests.post(url, json=payload).json()

            if not res or len(res) < 200: continue

            df = pd.DataFrame(res)
            df['close'], df['high'], df['low'] = df['c'].astype(float), df['h'].astype(float), df['l'].astype(float)

            # B. Indicator Math
            df['EMA_20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()

            ema_20, ema_50, ema_200 = df['EMA_20'].iloc[-1], df['EMA_50'].iloc[-1], df['EMA_200'].iloc[-1]
            s_h, s_l = df['high'].max(), df['low'].min()
            curr_px = df['close'].iloc[-1]
            range_pct = (s_h - s_l) / curr_px

            # C. Logic Gates
            is_up = (ema_20 > ema_50 > ema_200)
            is_down = (ema_20 < ema_50 < ema_200)

            if not (0.02 <= range_pct <= 0.045):
                print(f"⏭️ {coin} Volatility ({range_pct * 100:.2f}%) out of zone.")
                continue

            if not (is_up or is_down):
                print(f"⏭️ {coin} EMAs tangled.")
                continue

            # --- D. FIBONACCI & RISK MATH ---
            is_buy = True if is_up else False
            decimals = get_sz_decimals(coin, spot_meta)
            range_tot = s_h - s_l

            if is_buy:  # LONG
                entry_px = int(s_l + (range_tot * 0.618))
                d1_px, d2_px = int(s_l + (range_tot * 0.382)), int(s_l + (range_tot * 0.17))
            else:  # SHORT
                entry_px = int(s_h - (range_tot * 0.618))
                d1_px, d2_px = int(s_h - (range_tot * 0.382)), int(s_h - (range_tot * 0.17))

            # --- RISK LIMITER ---
            current_risk_size = BASE_DOLLAR_SIZE if range_pct < 0.035 else 10.0

            raw_sz = current_risk_size / entry_px
            p_factor = 10 ** decimals
            sz0 = math.floor(raw_sz * p_factor) / p_factor
            if sz0 == 0: sz0 = 1 / p_factor

            print(f"✅ {coin} {'LONG' if is_buy else 'SHORT'} | Risk: ${current_risk_size} | Entry: ${entry_px}")

            # E. Execution
            account = eth_account.Account.from_key(secret_key)
            exchange = Exchange(account, api_url, account_address=address, spot_meta=spot_meta)

            exchange.order(coin, is_buy, sz0, entry_px, {"limit": {"tif": "Gtc"}})
            exchange.order(coin, is_buy, sz0 * 2, d1_px, {"limit": {"tif": "Gtc"}})
            exchange.order(coin, is_buy, sz0 * 4, d2_px, {"limit": {"tif": "Gtc"}})
            print(f"🚀 {coin} Grid Armed on {env_mode.upper()}.")

        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()
