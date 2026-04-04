import os
import time
from dotenv import load_dotenv
import eth_account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# --- 1. SECURE INITIALIZATION ---
load_dotenv()
secret_key = os.getenv("HYPERLIQUID_SECRET_KEY")
if not secret_key:
    raise ValueError("❌ Missing HYPERLIQUID_SECRET_KEY in .env file")

account = eth_account.Account.from_key(secret_key)
wallet_address = account.address

print(f"🔌 Connecting Watchdog to Hyperliquid Mainnet for {wallet_address}...")

# These are the variables PyCharm was looking for!
info = Info(constants.MAINNET_API_URL, skip_ws=False)
exchange = Exchange(account, constants.MAINNET_API_URL)

import json

# --- 2. ACTIVE GRID MEMORY (Shared Brain) ---
def get_active_grids():
    """Reads the live trades saved by the FastAPI server."""
    try:
        with open("active_trades.json", "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# --- 3. THE COMPRESSION LOGIC (Spreadsheet Math) ---
def compress_take_profit(symbol: str, position_data: dict, manage_value: float, base_target_pct: float = 0.0407):
    """
    Calculates and places the new Take Profit using the Spreadsheet Formula:
    New TP = Avg Entry +/- ((Base Target % / Manage Value) * Avg Entry)
    """
    avg_entry = float(position_data['entryPx'])
    size = float(position_data['szi'])
    side = "LONG" if size > 0 else "SHORT"

    print(f"\n⚡ LIMIT FILL DETECTED ON {symbol}!")
    print(f"New Avg Entry: {avg_entry} | Total Size: {abs(size)}")

    # 1. Cancel the old Take Profit(s)
    try:
        open_orders = info.open_orders(wallet_address)
        tp_orders_to_cancel = [
            {"coin": symbol, "oid": order["oid"]}
            for order in open_orders
            if order["coin"] == symbol and order.get("isTrigger", False) == False
        ]
        if tp_orders_to_cancel:
            print(f"🗑️ Canceling {len(tp_orders_to_cancel)} old Take Profit orders...")
            exchange.cancel_orders(tp_orders_to_cancel)
    except Exception as e:
        print(f"⚠️ Error canceling old TP: {e}")

    # 2. THE SPREADSHEET MATH (A11 +/- ((D7/H11)*A11))
    new_tp_pct = base_target_pct / manage_value

    if side == "LONG":
        new_tp_price = avg_entry + (new_tp_pct * avg_entry)
    else:
        new_tp_price = avg_entry - (new_tp_pct * avg_entry)

    # Format to match exchange decimal requirements (4 decimals for most altcoins)
    new_tp_price = round(new_tp_price, 4)

    # 3. Place the New Compressed Take Profit
    is_buy = True if side == "SHORT" else False
    try:
        print(f"🎯 Placing NEW Compressed Take Profit at {new_tp_price} (Target: {round(new_tp_pct * 100, 2)}%)...")
        exchange.custom_order(
            symbol,
            is_buy,
            abs(size),
            new_tp_price,
            {"limit": {"tif": "Gtc"}, "reduceOnly": True}
        )
    except Exception as e:
        print(f"❌ Error placing new TP: {e}")


# --- 4. WEBSOCKET LISTENER ---
def user_events_callback(ws_data):
    data = ws_data.get("data", {})
    fills = data.get("fills", [])

    for fill in fills:
        symbol = fill.get("coin")
        active_grids = get_active_grids()  # Check the shared brain!

        if symbol in active_grids:
            print(f"🔔 WebSocket Alert: Order filled for {symbol} at {fill.get('px')}")

            try:
                user_state = info.user_state(wallet_address)
                positions = user_state.get("assetPositions", [])

                for pos in positions:
                    pos_data = pos.get("position", {})
                    if pos_data.get("coin") == symbol:
                        # Grab the exact manage value the server saved for this coin
                        manage_val = active_grids[symbol].get("manage1", 4)
                        compress_take_profit(symbol, pos_data, manage_val)
            except Exception as e:
                print(f"⚠️ Error fetching user state: {e}")


# Subscribe to the live feed
print("📡 Subscribing to Hyperliquid WebSocket feed...")
info.subscribe({"type": "userEvents", "user": wallet_address}, user_events_callback)

# Keep the script running forever
print("🛡️ Watchdog is online and listening. Press Ctrl+C to exit.")
while True:
    time.sleep(60)