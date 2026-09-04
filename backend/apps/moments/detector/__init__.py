"""Pure moment detection.

No Django, no ORM, no clock, no network. Given chat samples and an explicit
evaluation time, this package returns a score and its full working. Persistence
and cooldown belong to the services layer above it.
"""

from apps.moments.detector.config import DEFAULT_CONFIG, DetectorConfig
from apps.moments.detector.detector import MomentScore, passes_activity_gate, score_samples
from apps.moments.detector.window import ChatSample, DetectorInputError, WindowBounds

__all__ = [
    "DEFAULT_CONFIG",
    "ChatSample",
    "DetectorConfig",
    "DetectorInputError",
    "MomentScore",
    "WindowBounds",
    "passes_activity_gate",
    "score_samples",
]
