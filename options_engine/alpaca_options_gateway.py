from __future__ import annotations

import os
from datetime import date, timedelta, timezone, datetime
from typing import Dict, List

from dotenv import load_dotenv

from alpaca.data.enums import OptionsFeed
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetStatus,
    ContractType,
    OrderClass,
    PositionIntent,
    TimeInForce,
)
from alpaca.trading.requests import (
    GetOptionContractsRequest,
    LimitOrderRequest,
    OptionLegRequest,
)

from .config import PMCPConfig, BASE_DIR


load_dotenv(BASE_DIR / ".env", override=True)


def _clean_secret(value: str) -> str:
    return value.strip().strip('"').strip("'")


class AlpacaOptionsGateway:
    def __init__(self, config: PMCPConfig):
        self.config = config
        key = _clean_secret(
            os.getenv("ALPACA_API_KEY", "").strip()
            or os.getenv("APCA_API_KEY_ID", "").strip()
        )
        secret = _clean_secret(
            os.getenv("ALPACA_SECRET_KEY", "").strip()
            or os.getenv("APCA_API_SECRET_KEY", "").strip()
        )
        if not key or not secret:
            raise RuntimeError("Missing ALPACA_API_KEY/ALPACA_SECRET_KEY for options flow.")

        self.trading_client = TradingClient(
            api_key=key,
            secret_key=secret,
            paper=config.alpaca_paper_trade,
        )
        self.option_data_client = OptionHistoricalDataClient(api_key=key, secret_key=secret)

    def account(self):
        return self.trading_client.get_account()

    def ensure_account_ready(self) -> None:
        account = self.account()
        level = int(getattr(account, "options_trading_level", 0) or 0)
        approved = int(getattr(account, "options_approved_level", 0) or 0)
        if level < self.config.min_options_level:
            raise RuntimeError(
                f"Options trading level too low. required>={self.config.min_options_level} "
                f"effective={level} approved={approved}"
            )

    def list_put_contracts(self, underlying_symbol: str) -> List[object]:
        today = datetime.now(timezone.utc).date()
        min_exp = today + timedelta(days=self.config.short_put_dte_min)
        max_exp = today + timedelta(days=self.config.long_put_dte_max)

        page_token = None
        contracts: List[object] = []
        while True:
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying_symbol],
                status=AssetStatus.ACTIVE,
                type=ContractType.PUT,
                expiration_date_gte=min_exp.isoformat(),
                expiration_date_lte=max_exp.isoformat(),
                limit=1000,
                page_token=page_token,
            )
            resp = self.trading_client.get_option_contracts(req)
            batch = list(getattr(resp, "option_contracts", []) or [])
            contracts.extend(batch)
            page_token = getattr(resp, "next_page_token", None)
            if not page_token:
                break
        return contracts

    def fetch_put_chain_snapshots(self, underlying_symbol: str) -> Dict[str, object]:
        today = datetime.now(timezone.utc).date()
        min_exp = today + timedelta(days=self.config.short_put_dte_min)
        max_exp = today + timedelta(days=self.config.long_put_dte_max)

        feed = (
            OptionsFeed.OPRA
            if self.config.options_feed == "opra"
            else OptionsFeed.INDICATIVE
        )
        req = OptionChainRequest(
            underlying_symbol=underlying_symbol,
            feed=feed,
            type=ContractType.PUT,
            expiration_date_gte=min_exp.isoformat(),
            expiration_date_lte=max_exp.isoformat(),
        )
        return self.option_data_client.get_option_chain(req)

    def submit_pmcp_order(
        self,
        long_put_symbol: str,
        short_put_symbol: str,
        qty: int,
        limit_debit: float,
    ):
        order = LimitOrderRequest(
            order_class=OrderClass.MLEG,
            qty=float(qty),
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_debit, 2),
            legs=[
                OptionLegRequest(
                    symbol=long_put_symbol,
                    ratio_qty=1,
                    position_intent=PositionIntent.BUY_TO_OPEN,
                ),
                OptionLegRequest(
                    symbol=short_put_symbol,
                    ratio_qty=1,
                    position_intent=PositionIntent.SELL_TO_OPEN,
                ),
            ],
        )
        return self.trading_client.submit_order(order_data=order)

