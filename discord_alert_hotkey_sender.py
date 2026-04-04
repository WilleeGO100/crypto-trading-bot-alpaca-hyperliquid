from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

import keyboard
import pyperclip
import requests

POST_URL = os.getenv("POST_URL", "http://127.0.0.1:8000/signal")
HOTKEY = os.getenv("HOTKEY", "ctrl+shift+enter")
TIMEOUT_SEC = float(os.getenv("POST_TIMEOUT_SEC", "15.0"))

_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


@dataclass
class ParsedAlert:
    source: str
    symbol: str
    side: str  # LONG/SHORT
    entry: float
    limit1: Optional[float] = None
    limit2: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    manage1: Optional[int] = None
    manage2: Optional[int] = None
    raw_text: str = ""


def _value_after_colon(line: str) -> str:
    parts = line.split(":", 1)
    return parts[1].strip() if len(parts) == 2 else line.strip()


def _extract_float_value(line: str) -> Optional[float]:
    s = _value_after_colon(line)
    m = _NUM_RE.search(s)
    return float(m.group(1)) if m else None


def _extract_int_value(line: str) -> Optional[int]:
    s = _value_after_colon(line)
    m = re.search(r"(-?\d+)", s)
    return int(m.group(1)) if m else None


def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()

    # Proper JSON object
    if t.startswith("{") and t.endswith("}"):
        try:
            obj = json.loads(t)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None

    # Loose key/value JSON-ish block
    if '"symbol"' in t and '"side"' in t and '"entry"' in t:
        candidate = t.strip().strip(",")
        candidate = "{\n" + candidate + "\n}"
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None

    return None


def parse_from_json(obj: Dict[str, Any]) -> ParsedAlert:
    def gf(name: str) -> Optional[float]:
        v = obj.get(name)
        if v is None or v == "":
            return None
        return float(v)

    def gi(name: str) -> Optional[int]:
        v = obj.get(name)
        if v is None or v == "":
            return None
        return int(v)

    symbol = str(obj.get("symbol") or "").upper()
    side = str(obj.get("side") or "").upper()
    entry = obj.get("entry")

    if not symbol or side not in ("LONG", "SHORT") or entry is None:
        raise ValueError("JSON missing required fields: symbol, side, entry.")

    return ParsedAlert(
        source=str(obj.get("source") or "discord_tcl"),
        symbol=symbol,
        side=side,
        entry=float(entry),
        limit1=gf("limit1"),
        limit2=gf("limit2"),
        stop_loss=gf("stop_loss"),
        take_profit=gf("take_profit"),
        manage1=gi("manage1"),
        manage2=gi("manage2"),
        raw_text=str(obj.get("raw_text") or ""),
    )


def parse_tcl_alert(text: str) -> ParsedAlert:
    original = (text or "").strip()
    lines = [ln.strip() for ln in original.splitlines() if ln.strip()]

    symbol: Optional[str] = None
    side: Optional[str] = None

    entry: Optional[float] = None
    limit1: Optional[float] = None
    limit2: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    m1: Optional[int] = None
    m2: Optional[int] = None

    # Find: "ADAUSDT Long" or "ETHUSDT Short"
    for ln in lines:
        cleaned = ln.strip("*").strip()
        mm = re.match(r"^([A-Z0-9_:\-/.]+)\s+(Long|Short)\b", cleaned, re.IGNORECASE)
        if mm:
            symbol = mm.group(1).upper()
            side = "LONG" if mm.group(2).lower() == "long" else "SHORT"
            break

    for ln in lines:
        low = ln.lower()
        if low.startswith("entry"):
            entry = _extract_float_value(ln)
        elif low.startswith("limit 1"):
            limit1 = _extract_float_value(ln)
        elif low.startswith("limit 2"):
            limit2 = _extract_float_value(ln)
        elif low.startswith("stop loss") or low.startswith("stoploss") or low.startswith("sl"):
            sl = _extract_float_value(ln)
        elif low.startswith("take profit") or low.startswith("takeprofit") or low.startswith("tp"):
            tp = _extract_float_value(ln)
        elif low.startswith("manage 1"):
            m1 = _extract_int_value(ln)
        elif low.startswith("manage 2"):
            m2 = _extract_int_value(ln)

    if not symbol or not side:
        raise ValueError("Could not find the 'SYMBOL Side' line (e.g., 'ADAUSDT Long').")
    if entry is None:
        raise ValueError("Could not find Entry: <price>.")

    return ParsedAlert(
        source="discord_tcl",
        symbol=symbol,
        side=side,
        entry=float(entry),
        limit1=limit1,
        limit2=limit2,
        stop_loss=sl,
        take_profit=tp,
        manage1=m1,
        manage2=m2,
        raw_text=original,
    )


def post_signal(alert: ParsedAlert) -> Dict[str, Any]:
    payload = asdict(alert)
    payload["client_ts"] = time.time()

    # Do not raise_for_status; return body even for 400/500 so you can see why
    r = requests.post(POST_URL, json=payload, timeout=TIMEOUT_SEC)
    return {"status_code": r.status_code, "text": r.text}


def on_hotkey() -> None:
    text = (pyperclip.paste() or "").strip()
    if not text:
        print("[HOTKEY] Clipboard is empty.")
        return

    obj = _try_parse_json(text)
    try:
        if obj is not None:
            alert = parse_from_json(obj)
        else:
            alert = parse_tcl_alert(text)
    except Exception as e:
        print(f"[PARSE ERROR] {e}")
        print("---- Clipboard text ----")
        print(text)
        print("------------------------")
        return

    print("[PARSED]", json.dumps(asdict(alert), indent=2))

    try:
        resp = post_signal(alert)
        print(f"[POST] {resp['status_code']} {resp['text']}")
    except Exception as e:
        print(f"[POST ERROR] {e}")


def main() -> None:
    print(f"[READY] Copy the Discord alert text OR the JSON payload, then press: {HOTKEY}")
    print(f"[POST_URL] {POST_URL}")
    keyboard.add_hotkey(HOTKEY, on_hotkey)
    keyboard.wait()


if __name__ == "__main__":
    main()