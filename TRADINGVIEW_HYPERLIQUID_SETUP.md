# TradingView -> Hyperliquid Bridge

This repo includes `tv_hyperliquid_webhook.py`, which receives TradingView webhook alerts and executes them on Hyperliquid.

## 1) Configure `.env`

Add these keys:

```env
# required
TV_WEBHOOK_SECRET=replace-with-long-random-secret
TV_WEBHOOK_HOST=0.0.0.0
TV_WEBHOOK_PORT=8000

# execution defaults
TV_DEFAULT_SLIPPAGE=0.01
TV_DEFAULT_ORDER_USD=0
TV_ALLOW_SHORTS=true

# risk guards (0 disables each cap)
TV_MAX_USD_PER_ORDER=100
TV_MAX_COIN_PER_ORDER=0
TV_COOLDOWN_SECONDS=10

# duplicate protection
TV_IDEMPOTENCY_FILE=data/tv_webhook_seen_ids.json
TV_IDEMPOTENCY_TTL_SECONDS=21600
```

`HL_ENVIRONMENT`, `HL_SECRET_KEY`, `HL_TESTNET_SECRET_KEY`, and account addresses are reused from your existing environment config.

## 2) Start server

```powershell
python tv_hyperliquid_webhook.py
```

Health check:

```powershell
curl http://127.0.0.1:8000/health
```

## 3) Expose webhook endpoint publicly

TradingView needs HTTPS.

Example:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Use `https://<tunnel-domain>/webhook` in TradingView alert webhook URL.

## 4) Alert payloads supported

You can authenticate with either:
- HTTP header: `X-Webhook-Secret: <TV_WEBHOOK_SECRET>`
- JSON body field: `"secret":"<TV_WEBHOOK_SECRET>"`

### Open long
```json
{
  "alert_id": "tv-123",
  "secret": "YOUR_SECRET",
  "signal": "open_long",
  "symbol": "BTC",
  "usd_size": 50,
  "slippage": 0.01
}
```

### Open short
```json
{
  "alert_id": "tv-124",
  "secret": "YOUR_SECRET",
  "signal": "open_short",
  "symbol": "BTC",
  "usd_size": 50
}
```

### Close
```json
{
  "alert_id": "tv-125",
  "secret": "YOUR_SECRET",
  "signal": "close",
  "symbol": "BTC"
}
```

Also accepted:
- `action: buy/sell/close`
- `action: open` + `side: buy/sell`
- `size_usd` or `notional_usd` instead of `usd_size`

## 5) Pine message templates

Long entry alert message:

```text
{"alert_id":"{{time}}-L-{{ticker}}","secret":"YOUR_SECRET","signal":"open_long","symbol":"{{ticker}}","usd_size":50,"slippage":0.01}
```

Short entry alert message:

```text
{"alert_id":"{{time}}-S-{{ticker}}","secret":"YOUR_SECRET","signal":"open_short","symbol":"{{ticker}}","usd_size":50,"slippage":0.01}
```

Exit alert message:

```text
{"alert_id":"{{time}}-X-{{ticker}}","secret":"YOUR_SECRET","signal":"close","symbol":"{{ticker}}"}
```

## Safety checklist

- Start with paper/testnet and tiny size.
- Keep `TV_WEBHOOK_SECRET` private.
- Use dedicated automation keys.
- Set `TV_MAX_USD_PER_ORDER` and `TV_COOLDOWN_SECONDS` before going live.
