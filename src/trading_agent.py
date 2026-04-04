import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, Optional, Any

from anthropic import Anthropic, APIError

logger = logging.getLogger(__name__)


class TradingAgent:
    def __init__(self, config: Dict[str, Any], api_key: Optional[str] = None):
        self.config = config
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("Anthropic API key required")

        self.client = Anthropic(api_key=self.api_key)
        self.model = config.get("model_name", "claude-sonnet-4-5-20250929")

        tp = config.get("trading_params", {})
        rm = config.get("risk_management", {})

        self.min_risk_reward = tp.get("min_risk_reward", 2.0)
        self.confidence_threshold = tp.get("confidence_threshold", 0.55)
        self.stop_loss_min = rm.get("stop_loss_min", 12)
        self.stop_loss_default = rm.get("stop_loss_default", 20)
        self.stop_loss_max = rm.get("stop_loss_max", 60)
        self.stop_buffer = rm.get("stop_buffer", 5)

        logger.info(f"TradingAgent initialized (model={self.model})")

    def query_model_with_retry(self, prompt: str, max_retries: int = 5):
        base_delay = 2
        for attempt in range(max_retries):
            try:
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=5000,
                    temperature=0.2,
                    messages=[{"role": "user", "content": prompt}],
                )
            except APIError as e:
                msg = str(e).lower()
                retryable = any(x in msg for x in ("529", "429", "overloaded", "rate_limit"))
                if retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Retrying model call in {delay}s: {e}")
                    time.sleep(delay)
                    continue
                raise

    def build_prompt(
        self,
        fvg_context: Dict[str, Any],
        market_data: Dict[str, Any],
        level_context: Dict[str, Any],
        memory_context: Optional[Dict[str, Any]] = None,
        previous_analysis: Optional[str] = None,
    ) -> str:
        prompt = f"""
You are the final NQ futures decision engine.

Return ONLY valid JSON. No markdown.

CORE RULE:
Use gamma state FIRST, then use FVG / EMA / stochastic / structural levels to refine entry, stop, target, and confidence.

MARKET DATA:
- Price: {market_data.get('close', 0):.2f}
- EMA21: {market_data.get('ema21', 0):.2f}
- EMA75: {market_data.get('ema75', 0):.2f}
- EMA150: {market_data.get('ema150', 0):.2f}
- Stochastic: {market_data.get('stochastic', 50):.2f}

GAMMA CONTEXT:
- Market Regime: {level_context.get('market_regime')}
- Gamma State: {level_context.get('gamma_state')}
- Flip: {level_context.get('gamma_flip')}
- Call Wall: {level_context.get('major_call_wall')}
- Put Wall: {level_context.get('major_put_wall')}
- Dist To Flip: {level_context.get('dist_to_flip')}
- Dist To Call Wall: {level_context.get('dist_to_call_wall')}
- Dist To Put Wall: {level_context.get('dist_to_put_wall')}
- Inside Walls: {level_context.get('inside_walls')}

STRUCTURAL PRIORITIES:
1. In POSITIVE gamma, prefer mean reversion, fades, and tighter targets.
2. In NEGATIVE gamma, prefer continuation and pullback-with-trend setups.
3. Near the flip, reduce confidence and size unless a clear reclaim/reject is underway.
4. Treat call wall / put wall as target magnets or barriers.
5. Do not place targets beyond a nearby major wall unless breakout logic is explicit and high-confidence.
6. If inside compressed walls, reduce aggression and prefer rotational logic.
7. If the proposed trade fights both regime and flip position, reject it unless extremely compelling.

FVG CONTEXT:
{json.dumps(fvg_context, indent=2)}

LEVEL CONTEXT:
{json.dumps(level_context, indent=2)}

"""

        if previous_analysis:
            prompt += f"\nPREVIOUS ANALYSIS:\n{previous_analysis}\n"

        if memory_context:
            prompt += f"\nMEMORY CONTEXT:\n{json.dumps(memory_context, indent=2)}\n"

        prompt += f"""
DECISION REQUIREMENTS:
- Minimum risk/reward: {self.min_risk_reward}
- Confidence threshold target: {self.confidence_threshold}
- Stop distance bounds: {self.stop_loss_min} to {self.stop_loss_max}
- Default stop reference: {self.stop_loss_default}
- Stop buffer reference: {self.stop_buffer}

You must evaluate BOTH long and short, but only set primary_decision to LONG or SHORT if the chosen setup is truly actionable NOW.

Preferred setup_type values:
- GEX_CONTINUATION
- GEX_MEAN_REVERSION
- FLIP_RECLAIM
- FLIP_REJECT
- WALL_REJECTION
- WALL_TO_WALL_ROTATION
- FVG_WITH_GEX
- EMA_WITH_GEX
- NONE

JSON schema:
{{
  "current_bar_index": 0,
  "overall_bias": "bullish|bearish|neutral",
  "waiting_for": "string",
  "long_assessment": {{
    "status": "none|waiting|ready",
    "setup_type": "string|null",
    "entry_plan": null,
    "stop_plan": null,
    "raw_target": null,
    "target_plan": null,
    "risk_reward": null,
    "confidence": 0.0,
    "reasoning": "string"
  }},
  "short_assessment": {{
    "status": "none|waiting|ready",
    "setup_type": "string|null",
    "entry_plan": null,
    "stop_plan": null,
    "raw_target": null,
    "target_plan": null,
    "risk_reward": null,
    "confidence": 0.0,
    "reasoning": "string"
  }},
  "primary_decision": "LONG|SHORT|NONE",
  "overall_reasoning": "string"
}}

Important:
- Use wall-aware targets.
- Use flip-aware confidence.
- Use gamma regime to determine continuation vs mean reversion.
- Prefer no trade over a weak trade.
"""
        return prompt

    @staticmethod
    def _assessment_to_setup(assessment: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "setup_type": assessment.get("setup_type"),
            "entry": assessment.get("entry_plan"),
            "stop": assessment.get("stop_plan"),
            "raw_target": assessment.get("raw_target"),
            "target": assessment.get("target_plan"),
            "risk_reward": assessment.get("risk_reward"),
            "confidence": assessment.get("confidence", 0.0),
            "reasoning": assessment.get("reasoning", ""),
        }

    def parse_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        try:
            text = response_text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            decision = json.loads(text)

            if "long_assessment" in decision and "long_setup" not in decision:
                decision["long_setup"] = self._assessment_to_setup(decision["long_assessment"])
            if "short_assessment" in decision and "short_setup" not in decision:
                decision["short_setup"] = self._assessment_to_setup(decision["short_assessment"])

            if "primary_decision" not in decision:
                if decision.get("long_assessment", {}).get("status") == "ready":
                    decision["primary_decision"] = "LONG"
                elif decision.get("short_assessment", {}).get("status") == "ready":
                    decision["primary_decision"] = "SHORT"
                else:
                    decision["primary_decision"] = "NONE"

            return decision
        except Exception as e:
            logger.error(f"Failed to parse model response: {e}")
            return None

    def validate_decision(self, decision: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        required = ["overall_bias", "primary_decision", "long_setup", "short_setup", "overall_reasoning"]
        for field in required:
            if field not in decision:
                return False, f"Missing required field: {field}"

        if decision["primary_decision"] == "NONE":
            return True, None

        chosen = decision["long_setup"] if decision["primary_decision"] == "LONG" else decision["short_setup"]

        entry = chosen.get("entry")
        stop = chosen.get("stop")
        target = chosen.get("target")
        rr = chosen.get("risk_reward")
        conf = chosen.get("confidence", 0.0)

        if entry is None or stop is None or target is None:
            return False, "Chosen setup missing entry/stop/target"

        stop_distance = abs(entry - stop)
        if stop_distance < self.stop_loss_min:
            return False, f"Stop too tight: {stop_distance:.2f}"
        if stop_distance > self.stop_loss_max:
            return False, f"Stop too wide: {stop_distance:.2f}"

        if decision["primary_decision"] == "LONG" and stop >= entry:
            return False, "LONG stop must be below entry"
        if decision["primary_decision"] == "SHORT" and stop <= entry:
            return False, "SHORT stop must be above entry"

        if rr is None or rr < self.min_risk_reward:
            return False, f"Risk/reward too low: {rr}"
        if conf < self.confidence_threshold:
            return False, f"Confidence too low: {conf}"

        return True, None

    def analyze_setup(
        self,
        fvg_context: Dict[str, Any],
        market_data: Dict[str, Any],
        level_context: Dict[str, Any],
        memory_context: Optional[Dict[str, Any]] = None,
        previous_analysis: Optional[str] = None,
    ) -> Dict[str, Any]:
        prompt = self.build_prompt(
            fvg_context=fvg_context,
            market_data=market_data,
            level_context=level_context,
            memory_context=memory_context,
            previous_analysis=previous_analysis,
        )

        try:
            response = self.query_model_with_retry(prompt)
            response_text = response.content[0].text
            decision = self.parse_response(response_text)

            if not decision:
                return {
                    "success": False,
                    "error": "Failed to parse model response",
                    "raw_response": response_text,
                }

            is_valid, error_msg = self.validate_decision(decision)
            return {
                "success": is_valid,
                "decision": decision,
                "timestamp": datetime.now().isoformat(),
                "validation_error": error_msg,
                "raw_response": response_text,
            }

        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return {"success": False, "error": str(e)}