import time
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from hyperliquid_trade_planner import plan_and_execute_hyperliquid_trade

app = FastAPI(title="Hyperliquid Quant Engine Receiver")


class Signal(BaseModel):
    source: str = "discord_tcl"
    symbol: str
    side: str
    entry: float
    limit1: Optional[float] = None
    limit2: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    manage1: Optional[int] = None
    manage2: Optional[int] = None
    raw_text: str = ""
    client_ts: Optional[float] = None


# --- THE SNIPER DASHBOARD HTML & JS ---
HTML_CONTENT = """
<!DOCTYPE html>
<html>
<head>
    <title>Quant Engine Sniper</title>
    <style>
        body { background-color: #0f172a; color: #38bdf8; font-family: monospace; padding: 30px; }
        .container { max-width: 500px; margin: auto; background: #1e293b; padding: 25px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.8); }
        h2 { text-align: center; color: #f8fafc; margin-bottom: 25px; letter-spacing: 2px; }
        label { font-weight: bold; font-size: 14px; color: #94a3b8; }
        input, select { background: #0f172a; color: #f8fafc; border: 1px solid #38bdf8; padding: 12px; margin: 8px 0 20px 0; width: 100%; box-sizing: border-box; border-radius: 4px; font-family: monospace; font-size: 16px; }
        button { background: #22c55e; color: #fff; border: none; padding: 15px; font-size: 18px; width: 100%; cursor: pointer; font-weight: bold; border-radius: 4px; letter-spacing: 1px; transition: 0.2s; }
        button:hover { background: #16a34a; transform: scale(1.02); }
        .flex { display: flex; gap: 15px; }
        .flex > div { flex: 1; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🎯 SNIPER DASHBOARD</h2>

        <div class="flex">
            <div>
                <label>Symbol</label>
                <input type="text" id="symbol" placeholder="ADA" value="ADA">
            </div>
            <div>
                <label>Side</label>
                <select id="side" onchange="calculateLadder()">
                    <option value="LONG">LONG</option>
                    <option value="SHORT">SHORT</option>
                </select>
            </div>
        </div>

        <label>Entry Price</label>
        <input type="number" step="any" id="entry" placeholder="Type entry to auto-calculate..." oninput="calculateLadder()">

        <div class="flex">
            <div><label>Limit 1</label><input type="number" step="any" id="limit1"></div>
            <div><label>Limit 2</label><input type="number" step="any" id="limit2"></div>
        </div>

        <div class="flex">
            <div><label>Take Profit</label><input type="number" step="any" id="tp"></div>
            <div><label>Stop Loss</label><input type="number" step="any" id="sl"></div>
        </div>

        <button onclick="fireSquad()">🚀 FIRE SQUAD</button>
        <p id="status" style="text-align:center; margin-top:20px; font-size: 16px; font-weight: bold;"></p>
    </div>

    <script>
        // Auto-Calculator Logic
        function calculateLadder() {
            const entry = parseFloat(document.getElementById('entry').value);
            const side = document.getElementById('side').value;
            if(isNaN(entry)) return;

            // Tweak these percentages to match your strategy
            const L1_pct = 0.01;  // Limit 1 is 1% away
            const L2_pct = 0.02;  // Limit 2 is 2% away
            const TP_pct = 0.02;  // Take Profit is 2% away
            const SL_pct = 0.03;  // Stop Loss is 3% away

            let l1, l2, tp, sl;
            if (side === "LONG") {
                l1 = entry * (1 - L1_pct); l2 = entry * (1 - L2_pct);
                tp = entry * (1 + TP_pct); sl = entry * (1 - SL_pct);
            } else {
                l1 = entry * (1 + L1_pct); l2 = entry * (1 + L2_pct);
                tp = entry * (1 - TP_pct); sl = entry * (1 + SL_pct);
            }

            document.getElementById('limit1').value = l1.toFixed(4);
            document.getElementById('limit2').value = l2.toFixed(4);
            document.getElementById('tp').value = tp.toFixed(4);
            document.getElementById('sl').value = sl.toFixed(4);
        }

        // Execution Logic
        async function fireSquad() {
            const btn = document.querySelector('button');
            const status = document.getElementById('status');
            btn.disabled = true; btn.innerText = "EXECUTING...";
            status.innerText = "";

            const payload = {
                source: "sniper_dashboard",
                symbol: document.getElementById('symbol').value.toUpperCase(),
                side: document.getElementById('side').value,
                entry: parseFloat(document.getElementById('entry').value),
                limit1: parseFloat(document.getElementById('limit1').value) || null,
                limit2: parseFloat(document.getElementById('limit2').value) || null,
                take_profit: parseFloat(document.getElementById('tp').value) || null,
                stop_loss: parseFloat(document.getElementById('sl').value) || null,
                raw_text: "Manual execution via Sniper Dashboard"
            };

            try {
                const res = await fetch('/signal', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if(data.ok) {
                    status.style.color = "#22c55e";
                    status.innerText = "✅ Trade Executed on Hyperliquid!";
                } else {
                    status.style.color = "#ef4444";
                    status.innerText = "❌ Error: Check terminal logs";
                }
            } catch(e) {
                status.style.color = "#ef4444";
                status.innerText = "❌ Connection Failed";
            }
            setTimeout(() => { btn.disabled = false; btn.innerText = "🚀 FIRE SQUAD"; }, 2000);
        }
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    # This serves the HTML page when you visit the base URL
    return HTML_CONTENT


@app.post("/signal")
async def signal_endpoint(sig: Signal, request: Request):
    server_ts = time.time()
    payload = sig.model_dump()

    try:
        result = plan_and_execute_hyperliquid_trade(payload)
    except Exception as e:
        print(f"❌ Execution Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    print("\n=== 🚀 HYPERLIQUID SIGNAL PROCESSED ===")
    print(f"Source: {payload['source']}")
    print(f"Payload: {payload['symbol']} {payload['side']} @ {payload['entry']}")
    print(f"Result: {result}")
    print("===========================================\n")

    return {"ok": True, "server_ts": server_ts, "result": result}