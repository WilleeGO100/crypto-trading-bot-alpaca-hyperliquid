# PMCP Scaffold Notes

This scaffold is intentionally separate from BTC spot/perps flows.

## Strategy Shape

- Long dated deep ITM put (LEAPS-like exposure).
- Short nearer-dated OTM put against the long put.
- Net debit structure with bounded outlay (configured by `max_net_debit_per_spread`).

## Defaults

- Underlying: `SPY` (high liquidity proxy for index exposure).
- Runner: `run_options_pmcp.py`
- Config: `config/options_pmcp.json`
- Mode: paper + dry-run by default.

## Safety Defaults

- Requires options level check before planning/execution.
- Uses dry-run unless `OPTIONS_SUBMIT_ORDERS=true` and `OPTIONS_DRY_RUN=false`.
- Writes plan/state into `data/options/`.

## References

- Tastylive PMCP concept: https://www.tastylive.com/concepts-strategies/poor-man-covered-put
- Alpaca options guide/changelog: https://docs.alpaca.markets/changelog/v1-1

