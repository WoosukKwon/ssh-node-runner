"""scatter: Run commands on multiple SSH nodes with real-time monitoring."""

from .config import Config, NodeConfig, Defaults, load_config
from .executor import Executor, NodeStatus, NodeState

__all__ = [
    "Config",
    "NodeConfig",
    "Defaults",
    "load_config",
    "Executor",
    "NodeStatus",
    "NodeState",
]
