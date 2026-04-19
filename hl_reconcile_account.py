import argparse
import os
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

try:
    import eth_account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        f"Hyperliquid SDK imports failed: {exc}. Install dependencies first."
    )


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env.engine", override=True)
load_dotenv(BASE_DIR / ".env", override=False)


def _resolve_mode() -> str:
    mode = os.getenv("HL_ENVIRONMENT", "").strip().lower()
    if mode in {"live", "mainnet"}:
        return "live"
    if mode in {"paper", "testnet"}:
        return "paper"
    return "paper" if os.getenv("USE_TESTNET", "True").strip().lower() == "true" else "live"


def _mode_config() -> Dict[str, str]:
    mode = _resolve_mode()
    if mode == "live":
        secret = (
            os.getenv("HL_SECRET_KEY", "").strip()
            or os.getenv("HL_LIVE_SECRET_KEY", "").strip()
        )
        address = (
            os.getenv("HL_ACCOUNT_ADDRESS", "").strip()
            or os.getenv("HL_LIVE_ACCOUNT_ADDRESS", "").strip()
        )
        base_url = MAINNET_API_URL
    else:
        secret = (
            os.getenv("HL_TESTNET_SECRET_KEY", "").strip()
            or os.getenv("HL_PAPER_SECRET_KEY", "").strip()
        )
        address = (
            os.getenv("HL_TESTNET_ACCOUNT_ADDRESS", "").strip()
            or os.getenv("HL_PAPER_ACCOUNT_ADDRESS", "").strip()
        )
        base_url = TESTNET_API_URL
    return {"mode": mode, "secret": secret, "address": address, "base_url": base_url}


def _float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _print_positions(state: Dict[str, Any]) -> List[str]:
    non_zero_coins: List[str] = []
    positions = state.get("assetPositions", []) if isinstance(state, dict) else []
    if not positions:
        print("[INFO] No asset positions returned.")
        return non_zero_coins
    print("[INFO] Perp positions:")
    for p in positions:
        pos = p.get("position", {}) if isinstance(p, dict) else {}
        coin = str(pos.get("coin", ""))
        szi = _float(pos.get("szi"))
        entry = pos.get("entryPx")
        lev = pos.get("leverage", {})
        lev_text = f"{lev.get('type', '?')}:{lev.get('value', '?')}" if isinstance(lev, dict) else "?"
        print(f"  coin={coin} szi={szi} entry={entry} leverage={lev_text}")
        if coin and abs(szi) > 0:
            non_zero_coins.append(coin)
    return non_zero_coins


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose and reconcile Hyperliquid account positions/orders."
    )
    parser.add_argument("--address", default="", help="Override wallet/account address to inspect.")
    parser.add_argument("--close-all", action="store_true", help="Market-close every non-zero perp position.")
    parser.add_argument("--cancel-orders", action="store_true", help="Cancel all open perp orders.")
    parser.add_argument("--slippage", type=float, default=0.05, help="Market close slippage (default 0.05 = 5%%).")
    args = parser.parse_args()

    cfg = _mode_config()
    if not cfg["secret"]:
        raise SystemExit("[ERROR] Missing Hyperliquid secret key in env.")

    wallet = eth_account.Account.from_key(cfg["secret"])
    inspect_address = args.address.strip() or cfg["address"] or wallet.address
    print(f"[INFO] mode={cfg['mode']} base_url={cfg['base_url']}")
    print(f"[INFO] wallet={wallet.address}")
    print(f"[INFO] inspect_address={inspect_address}")

    info = Info(base_url=cfg["base_url"], skip_ws=True)
    exchange = Exchange(
        wallet=wallet,
        base_url=cfg["base_url"],
        account_address=inspect_address,
    )

    state = info.user_state(inspect_address)
    non_zero_coins = _print_positions(state)

    open_orders = info.open_orders(inspect_address)
    print(f"[INFO] open_perp_orders={len(open_orders) if isinstance(open_orders, list) else 0}")
    if isinstance(open_orders, list):
        for od in open_orders:
            print(
                f"  coin={od.get('coin')} oid={od.get('oid')} side={od.get('side')} "
                f"sz={od.get('sz')} px={od.get('limitPx')}"
            )

    try:
        spot_state = info.spot_user_state(inspect_address)
        balances = spot_state.get("balances", []) if isinstance(spot_state, dict) else []
        print(f"[INFO] spot_balances_count={len(balances)}")
    except Exception as exc:
        print(f"[WARN] spot_user_state failed: {exc}")

    if args.cancel_orders and isinstance(open_orders, list) and open_orders:
        reqs = []
        for od in open_orders:
            coin = od.get("coin")
            oid = od.get("oid")
            if coin is None or oid is None:
                continue
            reqs.append({"coin": coin, "oid": int(oid)})
        if reqs:
            print(f"[ACTION] Cancelling {len(reqs)} open orders...")
            resp = exchange.bulk_cancel(reqs)
            print(f"[RESULT] cancel={resp}")

    if args.close_all and non_zero_coins:
        print(f"[ACTION] Closing {len(non_zero_coins)} non-zero positions...")
        for coin in non_zero_coins:
            try:
                resp = exchange.market_close(coin=coin, slippage=args.slippage)
                print(f"[RESULT] close coin={coin} resp={resp}")
            except Exception as exc:
                print(f"[ERROR] close failed coin={coin}: {exc}")

        # Re-check after close attempts.
        print("[INFO] Rechecking positions after close attempts...")
        state2 = info.user_state(inspect_address)
        _print_positions(state2)


if __name__ == "__main__":
    main()

