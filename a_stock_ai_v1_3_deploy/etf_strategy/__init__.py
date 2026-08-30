"""Independent local ETF research and decision-support module.

This package intentionally has no dependency on the stock engine scheduler,
paper account, Feishu notifier, or Docker deployment.
"""

from etf_strategy.config import ETFStrategySettings, load_settings

__all__ = ["ETFStrategySettings", "load_settings"]
