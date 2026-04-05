from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from .config import PMCPConfig
from .models import OptionCandidate, PMCPPlan


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _build_candidate(
    contract: object,
    snapshot: object,
    spot: float,
) -> Optional[OptionCandidate]:
    quote = getattr(snapshot, "latest_quote", None)
    greeks = getattr(snapshot, "greeks", None)
    if quote is None or greeks is None:
        return None

    bid = _to_float(getattr(quote, "bid_price", 0.0), 0.0)
    ask = _to_float(getattr(quote, "ask_price", 0.0), 0.0)
    delta = _to_float(getattr(greeks, "delta", 0.0), 0.0)
    if bid <= 0 or ask <= 0:
        return None
    if delta == 0:
        return None

    expiration = getattr(contract, "expiration_date", None)
    strike = _to_float(getattr(contract, "strike_price", 0.0), 0.0)
    if expiration is None or strike <= 0:
        return None

    today = datetime.now(timezone.utc).date()
    dte = (expiration - today).days
    oi = int(_to_float(getattr(contract, "open_interest", 0), 0))
    return OptionCandidate(
        symbol=str(getattr(contract, "symbol", "")),
        strike=strike,
        expiration=expiration,
        delta=delta,
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2.0,
        open_interest=oi,
        dte=dte,
    )


def _closest_delta(
    candidates: Iterable[OptionCandidate],
    target_abs: float,
) -> Optional[OptionCandidate]:
    rows = list(candidates)
    if not rows:
        return None
    return min(rows, key=lambda c: abs(abs(c.delta) - target_abs))


def select_pmcp_plan(
    config: PMCPConfig,
    spot_price: float,
    contracts: List[object],
    snapshots: Dict[str, object],
) -> PMCPPlan:
    by_symbol = {str(getattr(c, "symbol", "")): c for c in contracts}

    candidates: List[OptionCandidate] = []
    for symbol, snap in snapshots.items():
        contract = by_symbol.get(symbol)
        if contract is None:
            continue
        cand = _build_candidate(contract, snap, spot_price)
        if cand is None:
            continue
        if cand.open_interest < config.min_open_interest:
            continue
        candidates.append(cand)

    if not candidates:
        raise RuntimeError("No option candidates with quotes/greeks/open-interest passed filters.")

    long_pool = [
        c
        for c in candidates
        if config.long_put_dte_min <= c.dte <= config.long_put_dte_max and c.strike >= spot_price
    ]
    short_pool = [
        c
        for c in candidates
        if config.short_put_dte_min <= c.dte <= config.short_put_dte_max and c.strike < spot_price
    ]
    if not long_pool:
        raise RuntimeError("No long put candidates matched long DTE/ITM filters.")
    if not short_pool:
        raise RuntimeError("No short put candidates matched short DTE/OTM filters.")

    long_sorted = sorted(
        long_pool, key=lambda c: abs(abs(c.delta) - config.target_long_delta_abs)
    )
    short_sorted = sorted(
        short_pool, key=lambda c: abs(abs(c.delta) - config.target_short_delta_abs)
    )

    best_pair = None
    best_pair_score = 10**9
    lowest_debit_seen = 10**9
    for long_put in long_sorted[:40]:
        for short_put in short_sorted[:80]:
            if short_put.expiration > long_put.expiration:
                continue
            net_debit = max(long_put.ask - short_put.bid, 0.0)
            if net_debit <= 0:
                continue
            lowest_debit_seen = min(lowest_debit_seen, net_debit)
            if net_debit > config.max_net_debit_per_spread:
                continue
            score = (
                abs(abs(long_put.delta) - config.target_long_delta_abs)
                + abs(abs(short_put.delta) - config.target_short_delta_abs)
            )
            if score < best_pair_score:
                best_pair = (long_put, short_put, net_debit)
                best_pair_score = score

    if best_pair is None:
        msg = (
            "No PMCP pair met the net debit cap. "
            f"lowest_observed_debit={lowest_debit_seen:.2f} "
            f"cap={config.max_net_debit_per_spread:.2f}"
        )
        raise RuntimeError(msg)

    long_put, short_put, net_debit = best_pair

    notes = (
        "Scaffold selection only. Long dated ITM put + short dated OTM put. "
        "Tune deltas/DTE/cost caps before automation."
    )
    return PMCPPlan(
        underlying=config.underlying_symbol,
        spot_price=spot_price,
        long_put=long_put,
        short_put=short_put,
        net_debit=round(net_debit, 2),
        estimated_capital=round(net_debit * 100 * config.contracts_qty, 2),
        notes=notes,
    )
