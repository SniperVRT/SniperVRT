"""Paper trading engine — realistic fills against persisted order books."""
from .executor import (
    PaperExecutor,
    PaperFill,
    PaperOrder,
    open_position_from_signal,
)

__all__ = ["PaperExecutor", "PaperFill", "PaperOrder", "open_position_from_signal"]
