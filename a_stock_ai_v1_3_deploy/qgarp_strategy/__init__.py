"""Independent, local-only Q-GARP A-share research and paper-trading strategy."""

from qgarp_strategy.config import QGARPSettings, load_settings
from qgarp_strategy.runner import QGARPStrategyRunner

__all__ = ["QGARPSettings", "QGARPStrategyRunner", "load_settings"]
