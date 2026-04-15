# Pine Script Alert Snippets (TradingView -> Hyperliquid)

Use these patterns inside your strategy to send webhook messages.

## 1) Define payload builder

```pine
//@version=5
strategy("My Strategy", overlay=true, calc_on_every_tick=true)

secret = input.string("REPLACE_ME", "Webhook Secret")
orderUsd = input.float(50, "Order USD")

buildPayload(signalType) =>
    '{"alert_id":"' + str.tostring(time) + '-' + signalType + '-' + syminfo.ticker +
    '","secret":"' + secret +
    '","signal":"' + signalType +
    '","symbol":"' + syminfo.basecurrency +
    '","usd_size":' + str.tostring(orderUsd) +
    ',"slippage":0.01}'
```

## 2) Send entry/exit alerts

```pine
longEntry = ta.crossover(ta.ema(close, 20), ta.ema(close, 50))
shortEntry = ta.crossunder(ta.ema(close, 20), ta.ema(close, 50))
exitSignal = ta.cross(close, ta.ema(close, 20))

if longEntry
    strategy.entry("L", strategy.long)
    alert(buildPayload("open_long"), alert.freq_once_per_bar_close)

if shortEntry
    strategy.entry("S", strategy.short)
    alert(buildPayload("open_short"), alert.freq_once_per_bar_close)

if exitSignal
    strategy.close_all()
    alert(buildPayload("close"), alert.freq_once_per_bar_close)
```

## 3) TradingView alert setup

- Condition: your strategy
- Trigger: `Any alert() function call`
- Webhook URL: `https://<your-public-url>/webhook`
- Message box: leave empty (payload is sent by `alert()` calls)
