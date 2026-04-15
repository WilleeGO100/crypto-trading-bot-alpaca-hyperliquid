import os
import time
import requests
import eth_account
import pandas as pd
import math
import traceback
import re
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


def _post_json(url: str, payload: dict):
    resp = requests.post(url, json=payload, timeout=15)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        body = (resp.text or "")[:300].replace("\n", " ")
        req_type = payload.get("type")
        raise requests.HTTPError(
            f"HTTP {resp.status_code} for payload={req_type}: body={body!r}",
            response=resp,
            request=resp.request,
        ) from e
    try:
        return resp.json()
    except ValueError as e:
        body = resp.text[:300].replace("\n", " ")
        raise ValueError(
            f"Invalid JSON response for payload={payload.get('type')} status={resp.status_code} body={body!r}"
        ) from e


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_env_mode() -> str:
    # Priority: explicit HL_ENVIRONMENT, fallback to legacy USE_TESTNET toggle.
    raw_mode = os.getenv("HL_ENVIRONMENT", "").strip().lower()
    if raw_mode in {"live", "mainnet"}:
        return "live"
    if raw_mode in {"paper", "testnet"}:
        return "paper"
    return "paper" if _env_bool("USE_TESTNET", default=True) else "live"


def _first_eth_address(*keys: str):
    for key in keys:
        val = os.getenv(key, "").strip()
        if val.startswith("0x"):
            return val, key
    return "", ""


def _is_eth_address(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", value.strip()))


def _urlish_env_entries(*keys: str):
    bad = []
    for key in keys:
        val = os.getenv(key, "").strip()
        if val.startswith("http://") or val.startswith("https://"):
            bad.append(f"{key}={val}")
    return bad


def _get_sz_decimals_from_exchange(exchange: Exchange, coin: str) -> int:
    try:
        asset = exchange.info.name_to_asset(coin)
        return int(exchange.info.asset_to_sz_decimals[asset])
    except Exception:
        return 5 if coin in ["BTC", "ETH"] else 2


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _trend_state(df: pd.DataFrame):
    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    ema50 = df["close"].ewm(span=50, adjust=False).mean()
    latest = float(df["close"].iloc[-1])
    fast = float(ema20.iloc[-1])
    slow = float(ema50.iloc[-1])
    if latest > fast > slow:
        return "LONG"
    if latest < fast < slow:
        return "SHORT"
    return "NEUTRAL"


def _smooth_trend_ok(df: pd.DataFrame) -> bool:
    atr = _atr(df, period=14)
    latest = float(df["close"].iloc[-1])
    if latest <= 0:
        return False
    atr_pct = atr / latest
    return 0.001 <= atr_pct <= 0.03


def _breakout_ok(df: pd.DataFrame, side: str, lookback: int = 20, breakout_buffer_pct: float = 0.0) -> bool:
    if len(df) < lookback + 2:
        return False
    recent = df.tail(lookback + 1)
    latest_close = float(recent["close"].iloc[-1])
    prior_high = float(recent["high"].iloc[:-1].max())
    prior_low = float(recent["low"].iloc[:-1].min())
    if side == "LONG":
        return latest_close >= prior_high * (1.0 + breakout_buffer_pct)
    return latest_close <= prior_low * (1.0 - breakout_buffer_pct)


def _weighted_avg(prices, qtys) -> float:
    total = sum(qtys)
    if total <= 0:
        return 0.0
    return sum(p * q for p, q in zip(prices, qtys)) / total


def _tp_from_avg(side: str, avg_price: float, target_profit_pct: float) -> float:
    return avg_price * (1.0 + target_profit_pct) if side == "LONG" else avg_price * (1.0 - target_profit_pct)


def _px_round(px: float) -> float:
    if px >= 1000:
        return round(px, 1)
    if px >= 100:
        return round(px, 2)
    if px >= 1:
        return round(px, 3)
    return round(px, 5)


def _parse_order_result(resp):
    if not isinstance(resp, dict):
        return False, f"unexpected_response_type={type(resp).__name__}", None
    if resp.get("status") != "ok":
        return False, f"api_status={resp.get('status')} details={resp}", None
    statuses = resp.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        return False, "missing_statuses", None
    status0 = statuses[0]
    if "error" in status0:
        return False, f"error={status0['error']}", None
    if "resting" in status0:
        oid = status0["resting"].get("oid")
        return True, f"resting oid={oid}", oid
    if "filled" in status0:
        filled = status0["filled"]
        return True, f"filled oid={filled.get('oid')} sz={filled.get('totalSz')} avgPx={filled.get('avgPx')}", filled.get("oid")
    return True, f"status={status0}", None


def _filled_qty_from_order_result(resp) -> float:
    if not isinstance(resp, dict):
        return 0.0
    statuses = resp.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        return 0.0
    status0 = statuses[0]
    if "filled" not in status0:
        return 0.0
    try:
        return float(status0["filled"].get("totalSz", 0.0))
    except Exception:
        return 0.0


def _coin_match(open_coin: str, target_coin: str) -> bool:
    a = str(open_coin or "").upper()
    b = str(target_coin or "").upper()
    return a == b or a.startswith(f"{b}:") or b in a


def _as_order_list(resp, label: str):
    if isinstance(resp, list):
        return [x for x in resp if isinstance(x, dict)]
    if isinstance(resp, dict):
        if "error" in resp:
            print(f"[WARN] {label} returned error: {resp.get('error')}")
            return []
        # Defensive fallback for alternate response envelopes.
        for key in ("orders", "data", "result"):
            maybe = resp.get(key)
            if isinstance(maybe, list):
                return [x for x in maybe if isinstance(x, dict)]
        print(f"[WARN] {label} unexpected response shape: {str(resp)[:220]}")
        return []
    print(f"[WARN] {label} unexpected response type: {type(resp).__name__}")
    return []


def _oid_is_openish(status_resp) -> bool:
    if not isinstance(status_resp, dict):
        return False
    status = status_resp.get("status")
    if isinstance(status, str):
        s = status.lower()
        return s in {"open", "resting", "triggered", "new"}
    order = status_resp.get("order")
    if isinstance(order, dict):
        for key in ("status", "state"):
            val = order.get(key)
            if isinstance(val, str) and val.lower() in {"open", "resting", "triggered", "new"}:
                return True
    return False


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _current_positions(ch_state):
    out = {}
    for row in ch_state.get("assetPositions", []):
        p = row.get("position", {})
        coin = str(p.get("coin", "")).split(":")[-1].upper()
        szi = _safe_float(p.get("szi", 0.0), 0.0)
        if not coin or szi == 0:
            continue
        out[coin] = {"szi": szi, "entry_px": _safe_float(p.get("entryPx", 0.0), 0.0)}
    return out


def _front_orders_for_coin(exchange: Exchange, address: str, coin: str):
    rows = _as_order_list(exchange.info.frontend_open_orders(address), f"frontend_open_orders[{address}]")
    return [o for o in rows if _coin_match(o.get("coin", ""), coin)]


def _ensure_exits(exchange: Exchange, address: str, coin: str, szi: float, entry_px: float, tp_pct: float):
    is_long = szi > 0
    qty = abs(szi)
    if qty <= 0 or entry_px <= 0:
        return

    coin_orders = _front_orders_for_coin(exchange, address, coin)
    reduce_orders = [o for o in coin_orders if bool(o.get("reduceOnly"))]
    entry_orders_open = len([o for o in coin_orders if not bool(o.get("reduceOnly"))])

    # Tighten stop only for first SL creation. After that, keep SL fixed.
    sl_pct_2p = float(os.getenv("TCL_SL_PCT_WITH_2PLUS_REMAINING", "0.012"))
    sl_pct_1 = float(os.getenv("TCL_SL_PCT_WITH_1_REMAINING", "0.009"))
    sl_pct_0 = float(os.getenv("TCL_SL_PCT_WITH_0_REMAINING", "0.006"))
    sl_pct = sl_pct_2p if entry_orders_open >= 2 else (sl_pct_1 if entry_orders_open == 1 else sl_pct_0)

    tp_px = _px_round(_tp_from_avg("LONG" if is_long else "SHORT", entry_px, tp_pct))
    existing_sl_px = None
    for o in reduce_orders:
        if bool(o.get("isTrigger")):
            existing_sl_px = _safe_float(o.get("triggerPx", 0.0), 0.0)
            if existing_sl_px > 0:
                break
    sl_px = (
        _px_round(existing_sl_px)
        if existing_sl_px and existing_sl_px > 0
        else _px_round(entry_px * (1.0 - sl_pct) if is_long else entry_px * (1.0 + sl_pct))
    )

    # Rebuild exits each monitor pass to keep qty/prices aligned with latest filled size.
    for o in reduce_orders:
        oid = o.get("oid")
        if oid is not None:
            try:
                exchange.cancel(coin, int(oid))
            except Exception:
                pass

    exchange.order(coin, not is_long, qty, tp_px, {"limit": {"tif": "Gtc"}}, reduce_only=True)
    exchange.order(
        coin,
        not is_long,
        qty,
        sl_px,
        {"trigger": {"triggerPx": sl_px, "isMarket": True, "tpsl": "sl"}},
        reduce_only=True,
    )
    print(
        f"[MANAGE] {coin} qty={qty:.6f} entry={entry_px:.6f} TP={tp_px} SL={sl_px} "
        f"(entry_orders_open={entry_orders_open}, sl_mode={'fixed' if existing_sl_px else f'new@{sl_pct:.4f}'})"
    )


def main():
    # 1. Load from .env and check master switch
    load_dotenv()
    env_mode = _resolve_env_mode()
    mode_source = "HL_ENVIRONMENT" if os.getenv("HL_ENVIRONMENT", "").strip() else "USE_TESTNET(defaulted if unset)"

    if env_mode == "live":
        # Info user (parent/main account) for /info queries.
        info_user_keys = (
            "HL_INFO_USER_ADDRESS",
            "HL_MAIN_ACCOUNT_ADDRESS",
            "HL_ACCOUNT_ADDRESS",
            "HL_MAINNET_ACCOUNT_ADDRESS",
            "HL_LIVE_ACCOUNT_ADDRESS",
            "HL_WALLET_ADDRESS",
        )
        secret_key_key = "HL_SECRET_KEY"
        secret_key = os.getenv(secret_key_key, "").strip()
        info_user, info_user_key = _first_eth_address(*info_user_keys)
        url = os.getenv("HL_MAINNET_INFO_URL", os.getenv("HL_LIVE_INFO_URL", os.getenv("HL_INFO_URL", "https://api.hyperliquid.xyz/info")))
        api_url = os.getenv("HL_MAINNET_API_URL", os.getenv("HL_LIVE_API_URL", constants.MAINNET_API_URL))
    else:
        # Info user (parent/main account) for /info queries.
        info_user_keys = (
            "HL_TESTNET_INFO_USER_ADDRESS",
            "HL_PAPER_INFO_USER_ADDRESS",
            "HL_TESTNET_MAIN_ACCOUNT_ADDRESS",
            "HL_TESTNET_ACCOUNT_ADDRESS",
            "HL_PAPER_ACCOUNT_ADDRESS",
            "HL_TESTNET_WALLET_ADDRESS",
            "HL_WALLET_ADDRESS",
        )
        secret_key_key = "HL_TESTNET_SECRET_KEY"
        secret_key = os.getenv(secret_key_key, "").strip()
        info_user, info_user_key = _first_eth_address(*info_user_keys)
        url = os.getenv("HL_TESTNET_INFO_URL", os.getenv("HL_PAPER_INFO_URL", "https://api.hyperliquid-testnet.xyz/info"))
        api_url = os.getenv("HL_TESTNET_API_URL", os.getenv("HL_PAPER_API_URL", constants.TESTNET_API_URL))

    # Derive signer wallet (API wallet/agent wallet) from the private key.
    signer_address = ""
    if secret_key:
        try:
            signer_address = eth_account.Account.from_key(secret_key).address
            print(f"[CFG] Signer wallet derived from {secret_key_key}.")
        except Exception:
            pass

    # Fallback for non-agent setups: if info user is unset, use signer address.
    if not info_user and signer_address:
        info_user = signer_address
        info_user_key = "fallback_to_signer_address"
        print(
            "[WARN] Info user address not set; defaulting to signer wallet address. "
            "For API-wallet setups, set HL_INFO_USER_ADDRESS to the parent account."
        )

    if not secret_key or not info_user:
        missing = []
        if not secret_key:
            missing.append(f"{secret_key_key}=<missing>")
        if not info_user:
            missing.append(f"info user address not found in any of: {', '.join(info_user_keys)}")
        urlish = _urlish_env_entries(*info_user_keys)
        urlish_msg = f" URL-like values found: {', '.join(urlish)}." if urlish else ""
        print(
            f"[ERROR] Missing credentials for {env_mode.upper()} mode. "
            f"Mode source: {mode_source}. "
            f"Missing: {'; '.join(missing)}. "
            f"Tried info-user key source: {info_user_key or 'none'}. "
            f"Set secret key + info user address (0x...) in .env."
            f"{urlish_msg}"
        )
        return
    if not info_user.startswith("0x"):
        print(
            f"[ERROR] {env_mode.upper()} info user address is invalid: {info_user!r}. "
            "Expected a wallet address like '0x...'. Check your .env keys."
        )
        return
    if not _is_eth_address(info_user):
        print(
            f"[ERROR] {env_mode.upper()} info user address format is invalid: {info_user!r}. "
            f"Length={len(info_user)}; expected 42 characters ('0x' + 40 hex chars). "
            f"Source key: {info_user_key or 'unknown'}."
        )
        if signer_address and _is_eth_address(signer_address):
            print(
                f"[HINT] Your signer wallet ({signer_address}) is valid. "
                "If you are not using a separate parent account, set the info-user key to that address."
            )
        return

    WATCHLIST = ['BTC', 'ETH', 'SOL', 'ARB', 'TIA']
    BASE_DOLLAR_SIZE = 15.0
    MAX_OPEN_TRADES = 3
    vol_min = float(os.getenv("TCL_VOL_MIN_PCT", "0.02"))
    vol_max = float(os.getenv("TCL_VOL_MAX_PCT", "0.045"))
    breakout_lookback = int(float(os.getenv("TCL_BREAKOUT_LOOKBACK", "20")))
    breakout_buffer_pct = float(os.getenv("TCL_BREAKOUT_BUFFER_PCT", "0.0"))
    min_order_value_usd = float(os.getenv("TCL_MIN_ORDER_VALUE_USD", "10"))
    enforce_entry_min = _env_bool("TCL_ENFORCE_ENTRY_MIN", default=True)
    breakout_lookback = max(2, breakout_lookback)
    breakout_buffer_pct = max(-0.02, min(0.02, breakout_buffer_pct))
    loop_enabled = _env_bool("TCL_LOOP_ENABLED", default=True)
    scan_interval_sec = int(float(os.getenv("TCL_SCAN_INTERVAL_SEC", "20")))
    monitor_interval_sec = int(float(os.getenv("TCL_MONITOR_INTERVAL_SEC", "8")))
    rest_when_in_position = _env_bool("TCL_REST_WHEN_IN_POSITION", default=True)
    rearm_cooldown_sec = int(float(os.getenv("TCL_REARM_COOLDOWN_SEC", "900")))
    rest_when_pending_entries = _env_bool("TCL_REST_WHEN_PENDING_ENTRIES", default=True)

    print(f"--- [BOT] Smyrna Station: Fibonacci Scanner ({env_mode.upper()}) ---")
    print(f"[CFG] Info endpoint: {url}")
    print(f"[CFG] Signer wallet: {signer_address or 'unknown'}")
    print(f"[CFG] Info user: {info_user} (source: {info_user_key or 'unknown'})")
    print(
        f"[CFG] Filters: vol_min={vol_min:.4f} vol_max={vol_max:.4f} "
        f"breakout_lookback={breakout_lookback} breakout_buffer_pct={breakout_buffer_pct:.4f}"
    )

    try:
        spot_meta = _post_json(url, {"type": "spotMeta"})
        account = eth_account.Account.from_key(secret_key)
        exchange = Exchange(account, api_url, account_address=info_user, spot_meta=spot_meta)
    except Exception as e:
        print(f"[WARN] Init Error: {e}")
        return

    # Local safety latch to prevent duplicate ladder submissions when state APIs lag.
    pending_entry_submissions = {}

    while True:
      fast_continue_to_monitor = False
      try:
        ch_payload = {"type": "clearinghouseState", "user": info_user}
        ch_state = _post_json(url, ch_payload)
      except Exception as e:
        print(f"[WARN] Sync Error: {e}")
        if loop_enabled:
            time.sleep(scan_interval_sec)
            continue
        return

      positions = _current_positions(ch_state)
      # Once a position is open, allow future re-arming after it eventually closes.
      for c in list(pending_entry_submissions.keys()):
          if c in positions:
              pending_entry_submissions.pop(c, None)
      if positions:
        print(f"[MONITOR] Open positions detected: {', '.join(sorted(positions.keys()))}")
        tp_pct = float(os.getenv("TCL_TARGET_PROFIT_PCT", "0.015"))
        for c, p in positions.items():
            try:
                _ensure_exits(exchange, info_user, c, p["szi"], p["entry_px"], tp_pct)
            except Exception as e:
                print(f"[WARN] {c} exit management failed: {e}")
        if rest_when_in_position:
            print(f"[SLEEP] monitor {monitor_interval_sec}s (rest mode while in position).")
            time.sleep(monitor_interval_sec)
            continue

      if pending_entry_submissions and rest_when_pending_entries:
        pending_coins = ", ".join(sorted(pending_entry_submissions.keys()))
        print(f"[MONITOR] Pending entry ladders detected: {pending_coins}")
        print(f"[SLEEP] monitor {monitor_interval_sec}s (rest mode while pending entries exist).")
        time.sleep(monitor_interval_sec)
        continue

      active_pos = [p for p in ch_state.get('assetPositions', []) if float(p['position']['szi']) != 0]
      if len(active_pos) >= MAX_OPEN_TRADES:
          print(f"[STOP] Position Limit ({MAX_OPEN_TRADES}) Reached.")
          if loop_enabled:
              time.sleep(scan_interval_sec)
              continue
          return

      for coin in WATCHLIST:
        print(f"\n[SCAN] Analyzing {coin}...")
        try:
            if coin in positions:
                print(f"[SKIP] {coin} Position already open; managed in monitor mode.")
                continue
            pending_ts = pending_entry_submissions.get(coin)
            if pending_ts:
                elapsed = time.time() - pending_ts
                if elapsed < rearm_cooldown_sec:
                    remain = int(rearm_cooldown_sec - elapsed)
                    print(f"[SKIP] {coin} Rearm cooldown active ({remain}s remaining).")
                    continue
                pending_entry_submissions.pop(coin, None)

            # A. Fetch Data
            now = int(time.time() * 1000)
            start = now - (200 * 5 * 60 * 1000)
            payload = {"type": "candleSnapshot",
                       "req": {"coin": coin, "interval": "5m", "startTime": start, "endTime": now}}
            res = _post_json(url, payload)

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
            trend_side = _trend_state(df)
            smooth_ok = _smooth_trend_ok(df)
            breakout_ok = (
                _breakout_ok(df, trend_side, lookback=breakout_lookback, breakout_buffer_pct=breakout_buffer_pct)
                if trend_side in {"LONG", "SHORT"}
                else False
            )

            if not (vol_min <= range_pct <= vol_max):
                print(f"[SKIP] {coin} Volatility ({range_pct * 100:.2f}%) out of zone.")
                continue

            if trend_side == "NEUTRAL":
                print(f"[SKIP] {coin} EMAs tangled.")
                continue

            if not smooth_ok:
                print(f"[SKIP] {coin} Trend not smooth enough.")
                continue

            if not breakout_ok:
                print(f"[SKIP] {coin} No continuation breakout yet.")
                continue

            # --- D. TCL-STYLE ENTRY/EXIT & ASYMMETRICAL STACKING ---
            is_buy = True if trend_side == "LONG" else False
            decimals = _get_sz_decimals_from_exchange(exchange, coin)
            swing_lookback = int(os.getenv("TCL_SWING_LOOKBACK", "80"))
            recent = df.tail(swing_lookback)
            swing_high, swing_low = float(recent['high'].max()), float(recent['low'].min())
            span = swing_high - swing_low
            if span <= 0:
                print(f"[SKIP] {coin} Invalid swing range.")
                continue

            # Entry is current market context; limits are pullback levels.
            entry_px = _px_round(float(curr_px))
            if is_buy:
                d1_px = _px_round(swing_high - span * 0.382)
                d2_px = _px_round(swing_high - span * 0.618)
            else:
                d1_px = _px_round(swing_low + span * 0.382)
                d2_px = _px_round(swing_low + span * 0.618)

            atr = _atr(df, period=14)
            stop_buffer_atr = float(os.getenv("TCL_STOP_BUFFER_ATR", "0.2"))
            stop_px = _px_round(swing_low - atr * stop_buffer_atr) if is_buy else _px_round(swing_high + atr * stop_buffer_atr)

            account_size = float(ch_state.get('marginSummary', {}).get('withdrawable', BASE_DOLLAR_SIZE))
            account_size_override = float(os.getenv("TCL_ACCOUNT_SIZE_OVERRIDE", "0"))
            if account_size_override > 0:
                account_size = account_size_override
            risk_pct = float(os.getenv("TCL_ACCOUNT_RISK_PCT", "2")) / 100.0
            risk_usd = max(1.0, account_size * risk_pct)

            limits_to_use = int(float(os.getenv("TCL_LIMITS_TO_USE", "2")))
            limits_to_use = max(0, min(2, limits_to_use))
            manage1 = float(os.getenv("TCL_MANAGE_1", "4"))
            manage2 = float(os.getenv("TCL_MANAGE_2", "7.3"))
            weights = [1.0]
            prices = [entry_px]
            if limits_to_use >= 1:
                weights.append(manage1)
                prices.append(d1_px)
            if limits_to_use >= 2:
                weights.append(manage2)
                prices.append(d2_px)

            total_w = sum(weights)
            avg_all = _weighted_avg(prices, weights)
            risk_per_unit = (avg_all - stop_px) if is_buy else (stop_px - avg_all)
            if risk_per_unit <= 0:
                print(f"[SKIP] {coin} Invalid stop distance.")
                continue
            total_qty = risk_usd / risk_per_unit

            p_factor = 10 ** decimals
            qtys = []
            for w in weights:
                q = total_qty * (w / total_w)
                q = math.floor(q * p_factor) / p_factor
                q = max(q, 1 / p_factor)
                qtys.append(q)

            # Keep TCL structure intact by ensuring base entry can clear venue min notional.
            if qtys:
                entry_notional = qtys[0] * entry_px
                if enforce_entry_min and entry_notional < min_order_value_usd:
                    scale = min_order_value_usd / max(entry_notional, 1e-12)
                    scaled = []
                    for q in qtys:
                        q2 = math.floor((q * scale) * p_factor) / p_factor
                        q2 = max(q2, 1 / p_factor)
                        scaled.append(q2)
                    qtys = scaled
                    if qtys[0] * entry_px < min_order_value_usd:
                        min_entry_qty = math.ceil((min_order_value_usd / entry_px) * p_factor) / p_factor
                        qtys[0] = max(qtys[0], min_entry_qty)
                    print(
                        f"[ADJUST] {coin} Scaled qtys x{scale:.2f} to enforce ENTRY "
                        f"minimum notional ${min_order_value_usd:.2f}."
                    )

            target_profit_pct = float(os.getenv("TCL_TARGET_PROFIT_PCT", "0.015"))
            tp_entry = _px_round(_tp_from_avg(trend_side, entry_px, target_profit_pct))
            tp_limit1 = _px_round(_tp_from_avg(trend_side, _weighted_avg(prices[:2], qtys[:2]), target_profit_pct)) if len(prices) >= 2 else tp_entry
            tp_limit2 = _px_round(_tp_from_avg(trend_side, _weighted_avg(prices, qtys), target_profit_pct))

            print(
                f"[OK] {coin} {trend_side} | Risk: ${risk_usd:.2f} ({risk_pct*100:.2f}%) | "
                f"Entry: ${entry_px} L1: ${d1_px} L2: ${d2_px} Stop: ${stop_px}"
            )
            print(
                f"[MATH] Manage1={manage1} Manage2={manage2} | TP(entry)={tp_entry} "
                f"TP(limit1fill)={tp_limit1} TP(limit2fill)={tp_limit2}"
            )

            # E. Execution
            # Entry + asymmetrical add-ons
            leg_results = []
            existing_front = _front_orders_for_coin(exchange, info_user, coin)
            non_reduce_existing = [o for o in existing_front if not bool(o.get("reduceOnly"))]
            if non_reduce_existing:
                print(f"[SKIP] {coin} Existing entry orders already open ({len(non_reduce_existing)}).")
                continue
            opening_legs = [("ENTRY", qtys[0], entry_px)]
            if limits_to_use >= 1:
                opening_legs.append(("DCA1", qtys[1], d1_px))
            if limits_to_use >= 2:
                opening_legs.append(("DCA2", qtys[2], d2_px))

            opening_accepted = 0
            opening_filled_qty = 0.0
            for leg_name, leg_qty, leg_px in opening_legs:
                notional = leg_qty * leg_px
                if notional < min_order_value_usd:
                    print(
                        f"[SKIP] {coin} {leg_name} notional ${notional:.2f} below minimum ${min_order_value_usd:.2f}."
                    )
                    continue
                leg_resp = exchange.order(coin, is_buy, leg_qty, leg_px, {"limit": {"tif": "Gtc"}})
                leg_results.append((leg_name, leg_resp))
                ok, _, _ = _parse_order_result(leg_resp)
                if ok:
                    opening_accepted += 1
                    opening_filled_qty += _filled_qty_from_order_result(leg_resp)

            # Exit logic: place reduce-only exits only if size is already filled.
            if opening_filled_qty > 0:
                r_tp = exchange.order(
                    coin,
                    not is_buy,
                    opening_filled_qty,
                    tp_entry,
                    {"limit": {"tif": "Gtc"}},
                    reduce_only=True,
                )
                leg_results.append(("TP", r_tp))
                r_sl = exchange.order(
                    coin,
                    not is_buy,
                    opening_filled_qty,
                    stop_px,
                    {"trigger": {"triggerPx": stop_px, "isMarket": True, "tpsl": "sl"}},
                    reduce_only=True,
                )
                leg_results.append(("SL", r_sl))
            elif opening_accepted > 0:
                pending_entry_submissions[coin] = time.time()
                print(
                    f"[INFO] {coin} Opening orders are resting; TP/SL deferred until a fill exists "
                    "(reduce-only exits require an open position)."
                )
            else:
                print(f"[WARN] {coin} No opening orders were accepted this cycle.")

            all_ok = True
            expected_oids = set()
            for leg_name, leg_resp in leg_results:
                ok, summary, oid = _parse_order_result(leg_resp)
                all_ok = all_ok and ok
                if ok and oid is not None:
                    expected_oids.add(int(oid))
                tag = "[ORDER]" if ok else "[ORDER-ERR]"
                print(f"{tag} {coin} {leg_name}: {summary}")

            try:
                open_orders = []
                frontend_orders = []
                query_addresses = [info_user]
                wallet_addr = account.address
                if wallet_addr and wallet_addr.lower() != info_user.lower():
                    query_addresses.append(wallet_addr)
                for attempt in range(3):
                    open_orders = []
                    frontend_orders = []
                    for q_addr in query_addresses:
                        oo_resp = exchange.info.open_orders(q_addr)
                        foo_resp = exchange.info.frontend_open_orders(q_addr)
                        open_orders.extend(_as_order_list(oo_resp, f"open_orders[{q_addr}]"))
                        frontend_orders.extend(_as_order_list(foo_resp, f"frontend_open_orders[{q_addr}]"))
                    all_oids = {
                        int(o.get("oid"))
                        for o in (open_orders + frontend_orders)
                        if isinstance(o, dict) and o.get("oid") is not None
                    }
                    if expected_oids.intersection(all_oids):
                        break
                    if attempt < 2:
                        time.sleep(0.8)

                open_oids = {
                    int(o.get("oid"))
                    for o in (open_orders + frontend_orders)
                    if isinstance(o, dict) and o.get("oid") is not None
                }
                matched_open = expected_oids.intersection(open_oids)
                oid_open_count = 0
                for oid in expected_oids:
                    for q_addr in query_addresses:
                        try:
                            oid_status = exchange.info.query_order_by_oid(q_addr, int(oid))
                            if _oid_is_openish(oid_status):
                                oid_open_count += 1
                                break
                        except Exception:
                            continue
                open_for_coin = [
                    o for o in (open_orders + frontend_orders)
                    if isinstance(o, dict) and _coin_match(o.get("coin", ""), coin)
                ]
                print(
                    f"[STATE] {coin} open_orders_on_platform={len(open_for_coin)} "
                    f"matched_submitted_oids={len(matched_open)} oid_open_count={oid_open_count} total_open_orders={len(open_oids)} "
                    f"query_addresses={','.join(query_addresses)}"
                )
                if expected_oids and len(matched_open) == 0 and oid_open_count == 0:
                    print(
                        f"[WARN] {coin} State API did not reflect newly accepted orders yet. "
                        f"Submitted resting oids: {sorted(expected_oids)}. "
                        "Treat order acknowledgements as source of truth for this cycle."
                    )
            except Exception as state_e:
                print(f"[WARN] {coin} Could not verify open orders: {state_e}")

            if all_ok:
                if opening_accepted > 0:
                    pending_entry_submissions[coin] = time.time()
                print(f"[START] {coin} TCL orders submitted successfully on {env_mode.upper()}.")
                if opening_accepted > 0 and rest_when_pending_entries:
                    print(
                        f"[MONITOR] Entering rest mode immediately after {coin} ladder submission "
                        "(pending entries active)."
                    )
                    fast_continue_to_monitor = True
                    break
            else:
                print(f"[WARN] {coin} Some order legs were rejected. Check [ORDER-ERR] lines above.")

        except Exception:
            traceback.print_exc()

      if not loop_enabled:
          break
      if fast_continue_to_monitor:
          continue
      print(f"[SLEEP] scan {scan_interval_sec}s")
      time.sleep(scan_interval_sec)


if __name__ == "__main__":
    main()
