"""Personal A-share quantitative research toolkit.

Signals are created after the close and are only eligible for the next session.
The package does not submit broker orders unless an explicit live gate is enabled.
"""

from .config import Settings
from .deepseek import DeepSeekResearchClient
from .sentiment import SentimentEngine

__all__ = ["Settings", "DeepSeekResearchClient", "SentimentEngine"]
