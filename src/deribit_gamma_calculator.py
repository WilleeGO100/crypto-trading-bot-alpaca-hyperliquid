"""
Deribit Bitcoin Options Gamma Calculator
Calculates gamma exposure levels from Deribit's free public API.
Returns gamma_flip, call_walls, put_walls similar to gexbot.com format.
"""

import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import requests

# =========================================================
# BLACK-SCHOLES MATH (mirrors fetch_spx_chain.py)
# =========================================================

SQRT_2PI = math.sqrt(2.0 * math.pi)
RISK_FREE_RATE = 0.05  # BTC markets ~5%
DIVIDEND_YIELD = 0.0  # No dividend for crypto
CONTRACT_MULTIPLIER = 1.0  # BTC options are 1 BTC per contract (not 100 like equities)

# Grid for gamma interpolation
GRID_PCT = 0.10  # ±10% around spot
GRID_POINTS = 401
MIN_OPTION_PRICE = 0.0001
MAX_DTE = 120  # Max days to expiration


def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / SQRT_2PI


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_d1(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """Black-Scholes d1 parameter."""
    return (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def bs_gamma(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """Black-Scholes gamma (second derivative of option price w.r.t. spot)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = bs_d1(S, K, T, r, q, sigma)
    return math.exp(-q * T) * norm_pdf(d1) / (S * sigma * math.sqrt(T))


def bs_price(S: float, K: float, T: float, r: float, q: float, sigma: float, option_type: str) -> float:
    """Black-Scholes option price."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        intrinsic = max(S - K, 0.0) if option_type == "C" else max(K - S, 0.0)
        return intrinsic

    d1 = bs_d1(S, K, T, r, q, sigma)
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "C":
        return math.exp(-q * T) * S * norm_cdf(d1) - math.exp(-r * T) * K * norm_cdf(d2)

    return math.exp(-r * T) * K * norm_cdf(-d2) - math.exp(-q * T) * S * norm_cdf(-d1)


def implied_vol_bisect(price: float, S: float, K: float, T: float, r: float, q: float, option_type: str) -> float:
    """Estimate implied volatility using bisection method."""
    if price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return math.nan

    intrinsic = max(S - K, 0.0) if option_type == "C" else max(K - S, 0.0)
    if price < intrinsic:
        return math.nan

    low = 1e-4
    high = 5.0

    low_price = bs_price(S, K, T, r, q, low, option_type)
    high_price = bs_price(S, K, T, r, q, high, option_type)

    if price < low_price - 1e-8 or price > high_price + 1e-8:
        return math.nan

    for _ in range(80):
        mid = 0.5 * (low + high)
        mid_price = bs_price(S, K, T, r, q, mid, option_type)

        if abs(mid_price - price) < 1e-6:
            return mid

        if mid_price < price:
            low = mid
        else:
            high = mid

    return 0.5 * (low + high)


def signed_gamma_at_spot(
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    iv: float,
    oi: float,
    option_type: str,
) -> float:
    """Signed gamma contribution from single option contract."""
    gamma = bs_gamma(S, K, T, r, q, iv)
    sign = 1.0 if option_type == "C" else -1.0
    return sign * gamma * oi * CONTRACT_MULTIPLIER


# =========================================================
# DERIBIT API INTEGRATION
# =========================================================

DERIBIT_BASE = "https://www.deribit.com/api/v2"


def fetch_btc_instruments() -> list[dict]:
    """Fetch all BTC option instruments from Deribit."""
    try:
        url = f"{DERIBIT_BASE}/public/get_instruments"
        params = {"currency": "BTC", "kind": "option"}
        response = requests.get(url, params=params, timeout=8)
        response.raise_for_status()
        return response.json().get("result", [])
    except Exception as e:
        print(f"[WARN] Deribit instruments fetch error: {e}")
        return []


def fetch_btc_ticker(instrument_name: str) -> Optional[Dict[str, Any]]:
    """Fetch current price and Greeks for a single BTC option from Deribit."""
    try:
        url = f"{DERIBIT_BASE}/public/ticker"
        params = {"instrument_name": instrument_name}
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        return response.json().get("result")
    except Exception as e:
        # Silently return None on timeout/error instead of printing
        return None


def get_btc_spot() -> Optional[float]:
    """Fetch current BTC spot price from Deribit."""
    try:
        url = f"{DERIBIT_BASE}/public/ticker"
        params = {"instrument_name": "BTC-PERPETUAL"}
        response = requests.get(url, params=params, timeout=8)
        response.raise_for_status()
        result = response.json().get("result", {})
        return float(result.get("last_price", 0.0))
    except Exception as e:
        print(f"[WARN] Deribit BTC spot fetch error: {e}")
        return None


# =========================================================
# GAMMA CALCULATION
# =========================================================

def compute_gamma_snapshot(spot: float, options_data: list[dict]) -> Dict[str, Optional[float]]:
    """
    Compute gamma exposure snapshot from BTC options data.
    Returns dict with gamma_flip, call_wall, put_wall, etc.
    """
    if not spot or spot <= 0:
        return _default_snapshot()

    if not options_data or len(options_data) < 10:
        return _default_snapshot()

    # Filter and process options
    df_rows = []
    now = datetime.now(timezone.utc).timestamp()

    for opt in options_data:
        try:
            # Parse instrument name: BTC-31MAR26-70000-C
            parts = opt.get("instrument_name", "").split("-")
            if len(parts) < 4:
                continue

            expiry_str = parts[1]  # e.g., "31MAR26"
            strike_str = parts[2]  # e.g., "70000"
            opt_type = parts[3].upper()  # C or P

            if opt_type not in ["C", "P"]:
                continue

            # Parse expiration
            try:
                expiry_dt = datetime.strptime(expiry_str, "%d%b%y")
                dte = (expiry_dt.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days
            except ValueError:
                continue

            if dte < 0 or dte > MAX_DTE:
                continue

            strike = float(strike_str)
            mid_price = float(opt.get("mid_price") or opt.get("last_price") or 0.0)
            open_interest = float(opt.get("open_interest") or 0.0)

            # Filter
            if mid_price < MIN_OPTION_PRICE or open_interest <= 0:
                continue

            # Strike band: ±20% around spot
            if strike < spot * 0.80 or strike > spot * 1.20:
                continue

            df_rows.append({
                "strike": strike,
                "mid_price": mid_price,
                "oi": open_interest,
                "dte": dte,
                "type": opt_type,
            })
        except (ValueError, TypeError, KeyError):
            continue

    if not df_rows:
        return _default_snapshot()

    # Calculate gamma for each option
    T_year = 1.0 / 365.0  # Use 1-day minimum
    results = []

    for row in df_rows:
        T = max(row["dte"] / 365.0, T_year)

        # Estimate IV from mid-price
        iv = implied_vol_bisect(
            price=row["mid_price"],
            S=spot,
            K=row["strike"],
            T=T,
            r=RISK_FREE_RATE,
            q=DIVIDEND_YIELD,
            option_type=row["type"],
        )

        if math.isnan(iv) or iv <= 0.001 or iv >= 5.0:
            continue

        # Calculate signed gamma
        sig_gamma = signed_gamma_at_spot(
            S=spot,
            K=row["strike"],
            T=T,
            r=RISK_FREE_RATE,
            q=DIVIDEND_YIELD,
            iv=iv,
            oi=row["oi"],
            option_type=row["type"],
        )

        results.append({
            "strike": row["strike"],
            "signed_gamma": sig_gamma,
            "type": row["type"],
            "oi": row["oi"],
        })

    if not results:
        return _default_snapshot()

    # Find gamma flip (where signed gamma crosses zero)
    df_sorted = sorted(results, key=lambda x: x["strike"])
    gamma_flip = _interpolate_gamma_flip([r["strike"] for r in df_sorted],
                                          [r["signed_gamma"] for r in df_sorted])

    # Find call & put walls (highest OI by type)
    calls = [r for r in results if r["type"] == "C"]
    puts = [r for r in results if r["type"] == "P"]

    call_wall = max((r["strike"] for r in calls), default=None) if calls else None
    if call_wall and calls:
        call_wall = max((r for r in calls if r["strike"] >= spot),
                       key=lambda x: x["oi"], default=calls[0])["strike"]

    put_wall = min((r["strike"] for r in puts), default=None) if puts else None
    if put_wall and puts:
        put_wall = max((r for r in puts if r["strike"] <= spot),
                      key=lambda x: x["oi"], default=puts[0])["strike"]

    # Handle case where gamma_flip is None
    if gamma_flip is None:
        regime = "UNKNOWN"
    else:
        regime = "POSITIVE" if spot > gamma_flip else "NEGATIVE"

    return {
        "spot_price": spot,
        "gamma_flip": gamma_flip,
        "major_call_wall": call_wall,
        "major_put_wall": put_wall,
        "market_regime": regime,
        "gamma_state": _classify_gamma_state(regime, spot, gamma_flip, call_wall, put_wall),
        "dist_to_flip": spot - gamma_flip if gamma_flip else None,
        "dist_to_call_wall": call_wall - spot if call_wall else None,
        "dist_to_put_wall": spot - put_wall if put_wall else None,
        "inside_walls": bool(call_wall and put_wall and put_wall <= spot <= call_wall),
    }


def _interpolate_gamma_flip(strikes: list[float], gammas: list[float]) -> Optional[float]:
    """Find gamma flip level by linear interpolation."""
    if not strikes or not gammas or len(strikes) < 2:
        return None

    for i in range(len(gammas) - 1):
        g1, g2 = gammas[i], gammas[i + 1]
        if g1 == 0:
            return float(strikes[i])
        if g1 * g2 < 0:  # Sign change
            s1, s2 = strikes[i], strikes[i + 1]
            return float(s1 - g1 * (s2 - s1) / (g2 - g1))

    return None


def _classify_gamma_state(regime: str, spot: float, flip: Optional[float],
                          call_wall: Optional[float], put_wall: Optional[float]) -> str:
    """Classify current gamma state."""
    if not flip:
        return "UNKNOWN"

    dist_to_flip = spot - flip
    inside_walls = bool(call_wall and put_wall and put_wall <= spot <= call_wall)

    if abs(dist_to_flip) <= 50:  # Within $50 of flip
        return "FLIP_TRANSITION"
    if inside_walls and (call_wall - put_wall) <= 200:  # Tight walls
        return "PINNED_INSIDE_WALLS"
    if regime == "NEGATIVE" and spot < flip:
        return "NEG_GAMMA_TREND_DOWN"
    if regime == "NEGATIVE" and spot > flip:
        return "NEG_GAMMA_UNSTABLE_ABOVE_FLIP"
    if regime == "POSITIVE" and inside_walls:
        return "POS_GAMMA_PINNING"
    if regime == "POSITIVE" and spot > flip:
        return "POS_GAMMA_ABOVE_FLIP"
    if regime == "POSITIVE" and spot < flip:
        return "POS_GAMMA_BELOW_FLIP"

    return "UNKNOWN"


def _default_snapshot() -> Dict[str, Optional[float]]:
    """Return default empty snapshot."""
    return {
        "spot_price": None,
        "gamma_flip": None,
        "major_call_wall": None,
        "major_put_wall": None,
        "market_regime": "UNKNOWN",
        "gamma_state": "UNKNOWN",
        "dist_to_flip": None,
        "dist_to_call_wall": None,
        "dist_to_put_wall": None,
        "inside_walls": None,
    }


# =========================================================
# PUBLIC API
# =========================================================

def get_btc_gamma_snapshot() -> Dict[str, Optional[float]]:
    """
    Main function: fetch BTC options and compute gamma exposure.
    Returns dict matching gexbot.com format for compatibility.

    Optimized to only fetch relevant strikes (ATM +/- 15%) to avoid excessive API calls.
    """
    spot = get_btc_spot()
    if not spot:
        return _default_snapshot()

    instruments = fetch_btc_instruments()
    if not instruments:
        return _default_snapshot()

    # Filter to only ATM strikes (±15%) to reduce API calls significantly
    relevant_instruments = []
    for inst in instruments:
        try:
            name = inst.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) < 4:
                continue

            # Parse strike
            strike = float(parts[2])

            # Keep only ATM options (±15%)
            if strike < spot * 0.85 or strike > spot * 1.15:
                continue

            relevant_instruments.append(inst)

        except (ValueError, IndexError):
            continue

    # Limit to ~80 instruments max to stay fast (further optimization)
    if len(relevant_instruments) > 80:
        relevant_instruments = relevant_instruments[:80]

    if not relevant_instruments:
        return _default_snapshot()

    # Fetch pricing for relevant instruments (with rate limiting)
    options_data = []
    for inst in relevant_instruments:
        time.sleep(0.05)  # Rate limit: ~20 req/sec
        ticker = fetch_btc_ticker(inst.get("instrument_name", ""))
        if ticker:
            ticker["instrument_name"] = inst.get("instrument_name")
            options_data.append(ticker)

    snapshot = compute_gamma_snapshot(spot, options_data)
    return snapshot


if __name__ == "__main__":
    # Quick test
    print("[SCAN] Fetching BTC gamma snapshot from Deribit...")
    snapshot = get_btc_gamma_snapshot()
    print(f"[OK] Snapshot: {snapshot}")
