from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class OptionCandidate:
    symbol: str
    strike: float
    expiration: date
    delta: float
    bid: float
    ask: float
    mid: float
    open_interest: int
    dte: int


@dataclass
class PMCPPlan:
    underlying: str
    spot_price: float
    long_put: OptionCandidate
    short_put: OptionCandidate
    net_debit: float
    estimated_capital: float
    notes: str

