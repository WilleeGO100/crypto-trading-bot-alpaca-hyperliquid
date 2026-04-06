import time
import threading
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


class HyperliquidWsManager:
    def __init__(self, symbol, address, exchange, limit2_id, tp_id, sl_id, original_entry, original_tp, exit_side,
                 total_qty):
        self.symbol = symbol
        self.address = address
        self.exchange = exchange

        self.limit2_id = limit2_id
        self.tp_id = tp_id
        self.sl_id = sl_id

        self.original_entry = original_entry
        self.original_tp = original_tp
        self.exit_side = exit_side  # "Buy" or "Sell"
        self.total_qty = total_qty

        self.info = Info(constants.MAINNET_API_URL, skip_ws=False)
        self._level2_hit = False
        self._stop_event = threading.Event()

    def start(self):
        print(f"[HL WS] Starting OCO & TP Compression Manager for {self.symbol}...")
        # Subscribe to private user events to watch for fills
        self.info.subscribe({"type": "userEvents", "user": self.address}, self._on_message)

        # Keep the manager thread alive
        threading.Thread(target=self._keep_alive, daemon=True).start()

    def _keep_alive(self):
        while not self._stop_event.is_set():
            time.sleep(1)

    def stop(self):
        print(f"[HL WS] Shutting down manager for {self.symbol}.")
        self._stop_event.set()

    def _on_message(self, message):
        # We only care about private user events
        if message.get("channel") != "userEvents":
            return

        data = message.get("data", {})
        order_updates = data.get("orderUpdates", [])

        for update in order_updates:
            order = update.get("order", {})
            oid = order.get("oid")
            status = order.get("status")  # "filled", "canceled", "open", etc.

            if status != "filled":
                continue

            # --- 1. OCO LOGIC: TP or SL Filled ---
            if oid == self.tp_id:
                print(f"[OCO] Take Profit hit! Canceling Stop Loss for {self.symbol}...")
                if self.sl_id:
                    self.exchange.cancel(self.symbol, self.sl_id)
                self.stop()
                return

            if oid == self.sl_id:
                print(f"[OCO] Stop Loss hit! Canceling Take Profit for {self.symbol}...")
                if self.tp_id:
                    self.exchange.cancel(self.symbol, self.tp_id)
                self.stop()
                return

            # --- 2. TP COMPRESSION LOGIC: Deepest DCA hit ---
            if oid == self.limit2_id and not self._level2_hit:
                self._level2_hit = True
                print(f"[COMPRESSION] Deepest DCA (Limit 2) filled! Moving TP to breakeven: {self.original_entry}")

                # Cancel the old greedy Take Profit
                if self.tp_id:
                    self.exchange.cancel(self.symbol, self.tp_id)

                # Place the new compressed Take Profit at the entry price
                is_buy = (self.exit_side == "Buy")
                try:
                    new_tp = self.exchange.order(
                        self.symbol,
                        is_buy,
                        self.total_qty,
                        self.original_entry,
                        {"limit": {"tif": "Gtc"}},
                        reduce_only=True
                    )

                    if new_tp["status"] == "ok":
                        # Extract the new order ID so OCO still works
                        statuses = new_tp["response"]["data"]["statuses"]
                        self.tp_id = statuses[0].get("resting", {}).get("oid")
                        print(f"[OK] TP Compressed successfully. New TP ID: {self.tp_id}")
                except Exception as e:
                    print(f"[ERROR] Failed to compress TP: {e}")