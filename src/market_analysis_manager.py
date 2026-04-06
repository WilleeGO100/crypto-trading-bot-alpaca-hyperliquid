"""
Market Analysis Manager Module
Manages persistent market analysis state and data loading for the trading engine.
"""

import os
import json
import logging
import pandas as pd
from typing import Dict, Optional, Any
from datetime import datetime
from pathlib import Path

# Setup logging
logger = logging.getLogger(__name__)

class MarketAnalysisManager:
    """Manages persistent market analysis state and ingestion of Databento data."""

    def __init__(self, analysis_file: str = "data/market_analysis.json"):
        """
        Initialize Market Analysis Manager
        """
        # Normalize and resolve path early to prevent invalid path errors
        self.analysis_file = Path(analysis_file).expanduser().resolve()

        # Ensure parent folder exists
        try:
            self.analysis_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.error(f"Failed to prepare analysis path {self.analysis_file}: {exc}")
            raise

        # Initialize or load existing AI analysis state
        self.current_analysis = self._load_analysis()

        logger.info(f"MarketAnalysisManager initialized (file={analysis_file})")

    # --- DATA LOADING METHODS ---

    def load_data(self):
        """
        Loads the Databento CSV from the data folder.
        This is the primary link between your fetch script and the analysis engine.
        """
        path = "data/HistoricalData.csv"

        if not os.path.exists(path):
            logger.error(f"[ERROR] Critical Error: {path} not found! Run your fetch script first.")
            return None

        try:
            # index_col='timestamp' handles the Databento time column
            df = pd.read_csv(path, index_col='timestamp', parse_dates=True)

            # Ensure the index is in a proper datetime format for time-based analysis
            df.index = pd.to_datetime(df.index)

            # Sort to ensure candles are in chronological order
            df = df.sort_index()

            logger.info(f"[OK] Successfully loaded {len(df)} candles from {path}")
            return df

        except Exception as e:
            logger.error(f"[ERROR] Failed to load market data: {e}")
            return None

    # --- PERSISTENCE & STATE METHODS ---

    def _get_empty_analysis(self) -> Dict[str, Any]:
        """Create initial empty analysis structure for the AI agent."""
        return {
            "last_updated": datetime.now().isoformat(),
            "current_bar_index": 0,
            "long_assessment": {
                "status": "none",
                "target_fvg": None,
                "entry_plan": None,
                "stop_plan": None,
                "target_plan": None,
                "reasoning": "No long setup identified yet",
                "confidence": 0.0,
                "setup_age_bars": 0
            },
            "short_assessment": {
                "status": "none",
                "target_fvg": None,
                "entry_plan": None,
                "stop_plan": None,
                "target_plan": None,
                "reasoning": "No short setup identified yet",
                "confidence": 0.0,
                "setup_age_bars": 0
            },
            "overall_bias": "neutral",
            "waiting_for": "Initial market analysis",
            "bars_since_last_trade": 0,
            "bars_since_last_update": 0
        }

    def _load_analysis(self) -> Dict[str, Any]:
        """Load existing analysis from file or create new if missing."""
        if self.analysis_file.exists():
            try:
                with open(str(self.analysis_file), 'r') as f:
                    analysis = json.load(f)
                    return analysis
            except Exception as e:
                logger.warning(f"Failed to load analysis file: {e}. Creating new.")
                return self._get_empty_analysis()
        return self._get_empty_analysis()

    def save_analysis(self, analysis: Optional[Dict[str, Any]] = None) -> bool:
        """Save analysis state to JSON for persistence across restarts."""
        if analysis is None:
            analysis = self.current_analysis

        try:
            analysis['last_updated'] = datetime.now().isoformat()
            with open(str(self.analysis_file), 'w', encoding='utf-8', newline='') as f:
                json.dump(analysis, f, indent=2)
            return True
        except Exception as e:
            logger.error(
                f"Failed to save analysis file {self.analysis_file}: {type(e).__name__}: {e}"
            )
            return False

    def update_analysis(self, new_analysis: Dict[str, Any]) -> bool:
        """Update the current analysis with new data from the agent."""
        try:
            # Track how old existing setups are
            if self.current_analysis.get('long_assessment', {}).get('status') != 'none':
                new_analysis['long_assessment']['setup_age_bars'] = \
                    self.current_analysis.get('long_assessment', {}).get('setup_age_bars', 0) + 1

            if self.current_analysis.get('short_assessment', {}).get('status') != 'none':
                new_analysis['short_assessment']['setup_age_bars'] = \
                    self.current_analysis.get('short_assessment', {}).get('setup_age_bars', 0) + 1

            new_analysis['bars_since_last_trade'] = self.current_analysis.get('bars_since_last_trade', 0) + 1
            new_analysis['bars_since_last_update'] = 0
            self.current_analysis = new_analysis
            return self.save_analysis()
        except Exception as e:
            logger.error(f"Failed to update analysis: {e}")
            return False

    def mark_trade_executed(self, direction: str):
        """Reset the analysis state after a trade is successfully taken."""
        self.current_analysis['bars_since_last_trade'] = 0
        if direction == "LONG":
            self.current_analysis['long_assessment']['status'] = 'none'
        elif direction == "SHORT":
            self.current_analysis['short_assessment']['status'] = 'none'
        self.save_analysis()

    def get_summary(self) -> str:
        """Get a human-readable summary of the current market regime/bias."""
        analysis = self.current_analysis
        lines = [
            "=" * 30,
            "MARKET ANALYSIS SUMMARY",
            "=" * 30,
            f"Bias: {analysis.get('overall_bias', 'neutral').upper()}",
            f"Waiting For: {analysis.get('waiting_for', 'N/A')}",
            f"Last Trade: {analysis.get('bars_since_last_trade', 0)} bars ago",
            "=" * 30
        ]
        return "\n".join(lines)

if __name__ == "__main__":
    # Test block to verify loading works
    logging.basicConfig(level=logging.INFO)
    manager = MarketAnalysisManager()
    data = manager.load_data()
    if data is not None:
        print(f"Sample Data:\n{data.head()}")
    print(manager.get_summary())