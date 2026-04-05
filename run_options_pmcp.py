"""
Standalone options scaffold for a Poor Man's Covered Put style structure.
Separate from BTC spot/perps runners.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

from options_engine.alpaca_options_gateway import AlpacaOptionsGateway
from options_engine.config import BASE_DIR, PMCPConfig
from options_engine.pmcp_strategy import select_pmcp_plan


CONFIG_PATH = BASE_DIR / "config" / "options_pmcp.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_spot_price(symbol: str) -> float:
    ticker = yf.Ticker(symbol)
    try:
        price = ticker.fast_info["last_price"]
        if price:
            return float(price)
    except Exception:
        pass
    history = ticker.history(period="1d", interval="1m", auto_adjust=False)
    if history.empty:
        raise RuntimeError(f"Unable to fetch spot price for {symbol}.")
    return float(history["Close"].iloc[-1])


def ensure_paths(cfg: PMCPConfig) -> None:
    Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
    state_path = Path(cfg.state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        state_path.write_text(
            json.dumps({"last_run": None, "last_order_id": None}, indent=2),
            encoding="utf-8",
        )
    csv_path = Path(cfg.plans_csv)
    if not csv_path.exists():
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "timestamp",
                    "underlying",
                    "spot",
                    "long_put",
                    "short_put",
                    "net_debit",
                    "estimated_capital",
                    "mode",
                ]
            )


def persist_plan(cfg: PMCPConfig, plan: dict, mode: str) -> None:
    Path(cfg.last_plan_json).write_text(json.dumps(plan, indent=2), encoding="utf-8")
    with Path(cfg.plans_csv).open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                utc_now_iso(),
                plan["underlying"],
                plan["spot_price"],
                plan["long_put"]["symbol"],
                plan["short_put"]["symbol"],
                plan["net_debit"],
                plan["estimated_capital"],
                mode,
            ]
        )


def update_state(cfg: PMCPConfig, order_id: str | None) -> None:
    payload = {
        "last_run": utc_now_iso(),
        "last_order_id": order_id,
    }
    Path(cfg.state_file).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    cfg = PMCPConfig.from_sources(CONFIG_PATH)
    ensure_paths(cfg)

    print("=" * 68)
    print("OPTIONS SCAFFOLD ONLINE (PMCP)")
    print("=" * 68)
    print(
        f"[CONFIG] underlying={cfg.underlying_symbol} broker={cfg.broker} "
        f"paper={cfg.alpaca_paper_trade} dry_run={cfg.dry_run} submit_orders={cfg.submit_orders} "
        f"feed={cfg.options_feed}"
    )
    print(
        "[CONFIG] "
        f"long_dte={cfg.long_put_dte_min}-{cfg.long_put_dte_max} "
        f"short_dte={cfg.short_put_dte_min}-{cfg.short_put_dte_max} "
        f"target_long_delta_abs={cfg.target_long_delta_abs} "
        f"target_short_delta_abs={cfg.target_short_delta_abs} "
        f"max_net_debit={cfg.max_net_debit_per_spread}"
    )

    gateway = AlpacaOptionsGateway(cfg)
    gateway.ensure_account_ready()
    acct = gateway.account()
    print(
        f"[ACCOUNT] status={getattr(acct, 'status', '?')} "
        f"options_level={getattr(acct, 'options_trading_level', '?')} "
        f"buying_power={getattr(acct, 'options_buying_power', '?')}"
    )

    spot = get_spot_price(cfg.underlying_symbol)
    contracts = gateway.list_put_contracts(cfg.underlying_symbol)
    snapshots = gateway.fetch_put_chain_snapshots(cfg.underlying_symbol)
    print(
        f"[DATA] spot={spot:.2f} contracts={len(contracts)} "
        f"snapshots={len(snapshots)}"
    )

    try:
        plan = select_pmcp_plan(
            config=cfg,
            spot_price=spot,
            contracts=contracts,
            snapshots=snapshots,
        )
    except Exception as exc:
        update_state(cfg, order_id=None)
        print(f"[PLAN] no_trade reason={exc}")
        return
    plan_dict = asdict(plan)
    print(
        f"[PLAN] long={plan.long_put.symbol} short={plan.short_put.symbol} "
        f"net_debit={plan.net_debit:.2f} est_capital={plan.estimated_capital:.2f}"
    )

    mode = "dry_run"
    order_id = None
    if cfg.submit_orders and not cfg.dry_run:
        order = gateway.submit_pmcp_order(
            long_put_symbol=plan.long_put.symbol,
            short_put_symbol=plan.short_put.symbol,
            qty=cfg.contracts_qty,
            limit_debit=plan.net_debit,
        )
        order_id = str(getattr(order, "id", ""))
        mode = "submitted"
        print(
            f"[ORDER] submitted id={order_id} status={getattr(order, 'status', None)} "
            f"class={getattr(order, 'order_class', None)}"
        )
    else:
        print("[ORDER] dry-run only. No order submitted.")

    persist_plan(cfg, plan_dict, mode)
    update_state(cfg, order_id=order_id)
    print(
        f"[OUTPUT] plan_json={cfg.last_plan_json} plans_csv={cfg.plans_csv} "
        f"state={cfg.state_file}"
    )


if __name__ == "__main__":
    main()
