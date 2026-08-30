"""Independent A-share hot-theme leader research and paper-trading strategy."""

from hot_leader_strategy.config import HotLeaderSettings, load_settings
from hot_leader_strategy.runner import HotLeaderRunner

__all__ = ["HotLeaderSettings", "HotLeaderRunner", "load_settings"]
