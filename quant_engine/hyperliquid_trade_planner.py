import os
import json
import eth_account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


def format_qty(qty, decimals=1):
    """Formats quantity to the exchange's required precision."""
    return round(float(qty), decimals)


def execute_trade_plan(parsed_signal):
    # --- 1. SETUP & AUTH ---
    secret_key = os.getenv("HYPERLIQUID_SECRET_KEY")
    account = eth_account.Account.from_key(secret_key)
    exchange = Exchange(account, constants.MAINNET_API_URL)

    symbol = parsed_signal.get("symbol")
    side = parsed_signal.get("side")
    entry_price = float(parsed_signal.get("entry"))
    is_buy = True if side == "LONG" else False

    # --- 2. FORCE CROSS MARGIN & LEVERAGE ---
    # Matches the 10X setting from your spreadsheet strategy
    leverage = 10
    try:
        print(f"[GUARD] Setting {symbol} to CROSS Margin at {leverage}x Leverage...")
        exchange.update_leverage(leverage, symbol, is_cross=True)
    except Exception as e:
        print(f"[WARN] Margin/Leverage already set or note: {e}")

    # --- 3. CYBORG SIZING LOGIC (1-3-5-8-10-15 Multipliers) ---
    # Uses $420 as the safe floor to satisfy the $10 minimum order rule
    notional_usd = float(parsed_signal.get("notional_usd", 420.0))

    # Identify active limits provided in the signal
    raw_limits = [
        parsed_signal.get("limit1"), parsed_signal.get("limit2"),
        parsed_signal.get("limit3"), parsed_signal.get("limit4"),
        parsed_signal.get("limit5")
    ]
    active_limits = [l for l in raw_limits if l is not None]

    # Scaling Multipliers based on your spreadsheet DNA
    multipliers = [1, 3, 5, 8, 10, 15]
    total_units = sum(multipliers[:len(active_limits) + 1])

    # Base unit value (1 unit) converted to coin quantity
    raw_base_qty = notional_usd / entry_price / total_units

    print(f"\n--- [BOT] CYBORG MATRIX PLANNER: {symbol} {side} ---")
    print(f"Total Units: {total_units} | Base Qty: {raw_base_qty}")

    # --- 4. EXECUTE INITIAL ENTRY (1x Unit) ---
    q0 = format_qty(raw_base_qty * multipliers[0])
    try:
        print(f"[START] Placing Base Entry {side} at {entry_price} (Size: {q0})...")
        exchange.custom_order(symbol, is_buy, q0, entry_price, {"limit": {"tif": "Gtc"}})
    except Exception as e:
        print(f"[ERROR] Base Entry Error: {e}")

    # --- 5. EXECUTE SCALED LIMITS (3x, 5x, 8x, etc.) ---
    for i, limit_price in enumerate(active_limits, start=1):
        size = format_qty(raw_base_qty * multipliers[i])
        try:
            print(f"[LADDER] Placing Limit {i} at {limit_price} (Size: {size})...")
            exchange.custom_order(symbol, is_buy, size, limit_price, {"limit": {"tif": "Gtc"}})
        except Exception as e:
            print(f"[ERROR] Limit {i} Error: {e}")

    # --- 6. PLACE INITIAL TAKE PROFIT & STOP LOSS ---
    tp = parsed_signal.get("take_profit")
    sl = parsed_signal.get("stop_loss")

    if tp:
        try:
            print(f"[TP] Placing Initial Take Profit at {tp}...")
            exchange.custom_order(symbol, not is_buy, q0, tp, {"limit": {"tif": "Gtc"}, "reduceOnly": True})
        except Exception as e:
            print(f"[ERROR] TP Error: {e}")

    if sl:
        try:
            print(f"[STOP] Placing Stop Loss at {sl}...")
            # SL is a trigger order (stopMarket)
            exchange.order(symbol, not is_buy, q0, sl, {"stopMarket": {"triggerPx": sl, "reduceOnly": True}})
        except Exception as e:
            print(f"[ERROR] SL Error: {e}")

    # --- 7. WRITE TO SHARED MEMORY FOR 24/7 WATCHDOG ---
    # This tells the position_manager.py how to handle TP compression
    manage1 = parsed_signal.get("manage1", 4)
    manage2 = parsed_signal.get("manage2", 10)

    memory_file = "active_trades.json"
    current_memory = {}

    if os.path.exists(memory_file):
        try:
            with open(memory_file, "r") as f:
                current_memory = json.load(f)
        except:
            pass

    current_memory[symbol] = {
        "side": side,
        "manage1": manage1,
        "manage2": manage2,
        "base_target_pct": 0.0407  # Matches the 4.07% risk from your spreadsheet
    }

    with open(memory_file, "w") as f:
        json.dump(current_memory, f, indent=4)

    print(f"[SAVE] {symbol} logged to Shared Memory. 24/7 Watchdog is now tracking.")
    return True