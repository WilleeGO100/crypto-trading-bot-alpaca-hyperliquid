"""
Second-stage agent selector with strict rule-based safety fallback.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

from market_scanner import RANKINGS_PATH, run_scan
from selector_rules import deterministic_pick, filter_safe_candidates

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
AGENT_SELECTION_PATH = DATA_DIR / "agent_selection.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_rankings() -> List[Dict[str, object]]:
    if RANKINGS_PATH.exists():
        try:
            payload = json.loads(RANKINGS_PATH.read_text(encoding="utf-8"))
            rankings = payload.get("rankings", [])
            if isinstance(rankings, list):
                return rankings
        except Exception:
            pass
    top_n = int(os.getenv("SCANNER_TOP_N", "5"))
    return run_scan(top_n=top_n).get("rankings", [])


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return cleaned.strip()


def _call_llm_pick(
    broker: str,
    safe_candidates: List[Dict[str, object]],
) -> Optional[Dict[str, object]]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.getenv("AGENT_MODEL", "gpt-5-mini").strip()
    timeout_seconds = float(os.getenv("AGENT_TIMEOUT_SECONDS", "8"))

    system = (
        "You are a trading symbol selector. Choose one symbol only from provided candidates. "
        "Do not invent symbols. Return strict JSON with keys: coin, confidence, rationale, risks."
    )
    user = {
        "broker": broker,
        "candidates": safe_candidates,
        "instruction": (
            "Pick the single best candidate for near-term tradability. "
            "Keep rationale concise and mention one risk."
        ),
    }

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user)},
                ],
                "temperature": 0.1,
            },
            timeout=timeout_seconds,
        )
        if resp.status_code >= 300:
            return None
        payload = resp.json()
        choices = payload.get("choices", [])
        if not choices:
            return None
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            return None
        data = json.loads(_strip_code_fences(content))
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def choose_symbol_with_agent(broker: str) -> Dict[str, str]:
    rankings = _load_rankings()
    safe, rejected = filter_safe_candidates(rankings, broker=broker)

    audit: Dict[str, object] = {
        "timestamp": _now_iso(),
        "broker": broker,
        "rankings_count": len(rankings),
        "safe_candidates_count": len(safe),
        "rejected": rejected,
        "selection_method": "",
        "agent_decision": None,
    }

    if not safe:
        selection = deterministic_pick(
            broker=broker,
            safe_candidates=[],
            reason="no_safe_candidates",
        )
        audit["selection_method"] = "deterministic_fallback"
        audit["selection"] = selection
        AGENT_SELECTION_PATH.write_text(json.dumps(audit, indent=2), encoding="utf-8")
        return selection

    use_agent = os.getenv("USE_AGENT_SELECTOR", "false").strip().lower() == "true"
    if not use_agent:
        selection = deterministic_pick(
            broker=broker,
            safe_candidates=safe,
            reason="deterministic_top_safe",
        )
        audit["selection_method"] = "deterministic"
        audit["selection"] = selection
        AGENT_SELECTION_PATH.write_text(json.dumps(audit, indent=2), encoding="utf-8")
        return selection

    llm_decision = _call_llm_pick(broker=broker, safe_candidates=safe)
    audit["agent_decision"] = llm_decision

    if isinstance(llm_decision, dict):
        chosen_coin = str(llm_decision.get("coin", "")).strip().upper()
        min_confidence = float(os.getenv("AGENT_CONFIDENCE_MIN", "0.65"))
        confidence = 0.0
        try:
            confidence = float(llm_decision.get("confidence", 0.0))
        except Exception:
            confidence = 0.0

        matched = [
            c for c in safe if str(c.get("coin", "")).strip().upper() == chosen_coin
        ]
        if matched and confidence >= min_confidence:
            selection = deterministic_pick(
                broker=broker,
                safe_candidates=matched,
                reason="agent_selected_validated",
            )
            audit["selection_method"] = "agent_validated"
            audit["selection"] = selection
            AGENT_SELECTION_PATH.write_text(json.dumps(audit, indent=2), encoding="utf-8")
            return selection

    selection = deterministic_pick(
        broker=broker,
        safe_candidates=safe,
        reason="agent_invalid_or_low_confidence_fallback",
    )
    audit["selection_method"] = "agent_fallback"
    audit["selection"] = selection
    AGENT_SELECTION_PATH.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return selection


if __name__ == "__main__":
    broker = os.getenv("BROKER", "hyperliquid").strip().lower()
    chosen = choose_symbol_with_agent(broker=broker)
    print(
        f"[AGENT_SELECTOR] broker={broker} coin={chosen['coin']} trade_symbol={chosen['trade_symbol']} reason={chosen['reason']}"
    )
