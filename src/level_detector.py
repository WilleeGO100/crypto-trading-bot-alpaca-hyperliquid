import logging
from datetime import datetime
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


class LevelDetector:
    def __init__(self, level_intervals: Optional[List[int]] = None, confluence_tolerance: float = 10.0):
        self.level_intervals = level_intervals or [100]
        self.confluence_tolerance = float(confluence_tolerance)

    @staticmethod
    def round_to_level(price: float, interval: int) -> int:
        return int(round(price / interval) * interval)

    def find_nearest_levels(self, current_price: float, interval: int) -> Dict[str, Any]:
        nearest_level = self.round_to_level(current_price, interval)

        if current_price == nearest_level:
            level_above = nearest_level + interval
            level_below = nearest_level - interval
        elif current_price < nearest_level:
            level_above = nearest_level
            level_below = nearest_level - interval
        else:
            level_above = nearest_level + interval
            level_below = nearest_level

        return {
            "nearest_level": nearest_level,
            "level_above": level_above,
            "level_below": level_below,
            "distance_above": level_above - current_price,
            "distance_below": current_price - level_below,
            "on_level": abs(current_price - nearest_level) <= 1.0,
        }

    @staticmethod
    def _nearest_resistance(price: float, candidates: List[Optional[float]]) -> Optional[float]:
        valid = [float(x) for x in candidates if isinstance(x, (int, float)) and float(x) >= price]
        return min(valid) if valid else None

    @staticmethod
    def _nearest_support(price: float, candidates: List[Optional[float]]) -> Optional[float]:
        valid = [float(x) for x in candidates if isinstance(x, (int, float)) and float(x) <= price]
        return max(valid) if valid else None

    def analyze_level_context(self, current_price: float, gamma_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        interval = int(self.level_intervals[0])
        ems = self.find_nearest_levels(current_price, interval)

        gamma_flip = gamma_context.get("gamma_flip") if gamma_context else None
        call_wall = gamma_context.get("major_call_wall") if gamma_context else None
        put_wall = gamma_context.get("major_put_wall") if gamma_context else None

        resistance = self._nearest_resistance(current_price, [call_wall, gamma_flip, ems["level_above"]])
        support = self._nearest_support(current_price, [put_wall, gamma_flip, ems["level_below"]])

        dist_to_resistance = None if resistance is None else resistance - current_price
        dist_to_support = None if support is None else current_price - support

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "current_price": current_price,
            "nearest_level": ems["nearest_level"],
            "nearest_level_above": ems["level_above"],
            "nearest_level_below": ems["level_below"],
            "distance_to_level_above": ems["distance_above"],
            "distance_to_level_below": ems["distance_below"],
            "on_level": ems["on_level"],
            "gamma_flip": gamma_flip,
            "major_call_wall": call_wall,
            "major_put_wall": put_wall,
            "nearest_structural_resistance": resistance,
            "nearest_structural_support": support,
            "distance_to_resistance": dist_to_resistance,
            "distance_to_support": dist_to_support,
        }

    def score_confluence(
        self,
        side: str,
        entry_price: float,
        level_context: Dict[str, Any],
        gamma_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        score = 0.0
        reasons: List[str] = []

        regime = str(gamma_context.get("market_regime", "UNKNOWN")).upper()
        gamma_state = str(gamma_context.get("gamma_state", "UNKNOWN")).upper()
        inside_walls = bool(gamma_context.get("inside_walls", False))
        dist_to_flip = gamma_context.get("dist_to_flip")

        resistance = level_context.get("nearest_structural_resistance")
        support = level_context.get("nearest_structural_support")
        dist_to_resistance = level_context.get("distance_to_resistance")
        dist_to_support = level_context.get("distance_to_support")

        if side == "BUY":
            if regime == "POSITIVE":
                score += 0.35
                reasons.append("long aligned with positive gamma")
            if gamma_state in {"POS_GAMMA_PINNING", "POS_GAMMA_ABOVE_FLIP", "FLIP_TRANSITION"}:
                score += 0.15
                reasons.append(f"state supports long: {gamma_state}")
            if dist_to_resistance is not None and dist_to_resistance >= self.confluence_tolerance:
                score += 0.20
                reasons.append("room to resistance")
            elif dist_to_resistance is not None:
                score -= 0.15
                reasons.append("tight overhead resistance")
            if support is not None and entry_price > support:
                score += 0.10
                reasons.append("support underneath")
            if inside_walls and gamma_state != "POS_GAMMA_PINNING":
                score -= 0.10
                reasons.append("inside walls without pinning bias")

        if side == "SELL":
            if regime == "NEGATIVE":
                score += 0.35
                reasons.append("short aligned with negative gamma")
            if gamma_state in {"NEG_GAMMA_TREND_DOWN", "NEG_GAMMA_UNSTABLE_ABOVE_FLIP", "FLIP_TRANSITION"}:
                score += 0.15
                reasons.append(f"state supports short: {gamma_state}")
            if dist_to_support is not None and dist_to_support >= self.confluence_tolerance:
                score += 0.20
                reasons.append("room to support target")
            elif dist_to_support is not None:
                score -= 0.15
                reasons.append("tight support underneath")
            if resistance is not None and entry_price < resistance:
                score += 0.10
                reasons.append("resistance overhead")
            if inside_walls and gamma_state != "NEG_GAMMA_TREND_DOWN":
                score -= 0.10
                reasons.append("inside walls without strong breakdown bias")

        if isinstance(dist_to_flip, (int, float)) and abs(float(dist_to_flip)) <= self.confluence_tolerance:
            score -= 0.10
            reasons.append("near gamma flip")

        return {
            "score": max(0.0, min(1.0, score)),
            "reasons": reasons,
        }