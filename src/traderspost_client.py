import json
import logging
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger(__name__)


class TradersPostClient:
    def __init__(self, webhook_url: str, enable_live_execution: bool = False):
        self.webhook_url = webhook_url
        self.enable_live_execution = enable_live_execution
        self.headers = {"Content-Type": "application/json"}

    def send_bracket_order(
        self,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        target: float,
        quantity: int = 1,
        test_mode: bool = False,
        strategy_name: str = "Quant-X Global Alpha",
        gamma_context: Optional[Dict[str, Any]] = None,
        setup_type: str = "UNKNOWN",
    ) -> bool:
        action = "buy" if side == "LONG" else "sell"

        rr = 0.0
        try:
            rr = abs(target - entry) / abs(entry - stop)
        except Exception:
            rr = 0.0

        payload = {
            "ticker": symbol,
            "action": action,
            "orderType": "market",
            "quantity": quantity,
            "quantityType": "fixed_quantity",
            "test": test_mode,
            "stopLoss": {"price": round(stop, 2)},
            "takeProfit": {"price": round(target, 2)},
            "extras": {
                "strategy": strategy_name,
                "setup_type": setup_type,
                "calculated_rr": round(rr, 2),
                "gamma_state": gamma_context.get("gamma_state") if gamma_context else None,
                "market_regime": gamma_context.get("market_regime") if gamma_context else None,
                "gamma_flip": gamma_context.get("gamma_flip") if gamma_context else None,
                "call_wall": gamma_context.get("major_call_wall") if gamma_context else None,
                "put_wall": gamma_context.get("major_put_wall") if gamma_context else None,
                "dist_to_flip": gamma_context.get("dist_to_flip") if gamma_context else None,
                "inside_walls": gamma_context.get("inside_walls") if gamma_context else None,
            },
        }

        logger.info(f"TradersPost payload: {json.dumps(payload)}")

        if not self.enable_live_execution:
            logger.info("Live execution disabled. Webhook simulated.")
            return True

        try:
            response = requests.post(self.webhook_url, json=payload, headers=self.headers, timeout=10)
            response.raise_for_status()
            logger.info(f"Webhook fired successfully: {response.text}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send webhook to TradersPost: {e}")
            return False