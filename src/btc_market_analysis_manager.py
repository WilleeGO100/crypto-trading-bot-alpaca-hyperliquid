"""Bitcoin-specific analysis persistence helper."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .market_analysis_manager import MarketAnalysisManager


class BTCMarketAnalysisManager(MarketAnalysisManager):
    """Minor wrapper so BTC live runs write to their own analysis file."""

    DEFAULT_FILE = "data/btc_market_analysis.json"

    def __init__(self, analysis_file: Optional[str] = None):
        if not analysis_file:
            analysis_file = self.DEFAULT_FILE
        super().__init__(analysis_file)

    @staticmethod
    def build_market_snapshot(
        latest_bar: Dict[str, Any],
        gamma_context: Dict[str, Any],
        level_context: Dict[str, Any],
        setup: Optional[Dict[str, Any]] = None,
        decision: Optional[Dict[str, Any]] = None,
        engine_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latest_bar": latest_bar,
            "gamma_context": gamma_context,
            "level_context": level_context,
            "setup": setup,
            "decision": decision,
            "engine_state": engine_state or {},
        }
