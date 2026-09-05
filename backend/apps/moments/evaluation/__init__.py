"""Offline evaluation and calibration for the moment detector.

Milestone 5 proved the detector computes what its formulas say. This layer asks
the other question — whether what it computes corresponds to moments a person
would actually want clipped — by replaying collected chat against human labels
and reporting how well they agree.

Three properties hold throughout:

* **It observes.** No function here writes a row, and none of them contacts
  Twitch. Evaluation cannot create a clip, a candidate or a session.
* **It does not fork the detector.** Every score comes from `score_samples`,
  the same function production calls. This package adds the grid, the ground
  truth and the arithmetic around it, and nothing else.
* **It reports aggregates.** No chat text and no chatter identity leaves this
  layer, in console output or in a written report.
"""

from apps.moments.evaluation.comparison import (
    ConfigurationResult,
    compare_configurations,
    evaluate_configuration,
    rank,
)
from apps.moments.evaluation.config import (
    BASELINE_NAME,
    NamedConfig,
    baseline,
    load_config_file,
    named_config,
)
from apps.moments.evaluation.errors import (
    ConfigFileError,
    EvaluationError,
    LabelFileError,
    ReplayError,
)
from apps.moments.evaluation.labels import Label, LabelSet, load_labels
from apps.moments.evaluation.matching import Match, MatchResult, match_candidates
from apps.moments.evaluation.metrics import Metrics, compute_metrics
from apps.moments.evaluation.replay import (
    DEFAULT_CADENCE_SECONDS,
    Observation,
    ReplayResult,
    load_session_samples,
    replay_samples,
    replay_session,
)

__all__ = [
    "BASELINE_NAME",
    "DEFAULT_CADENCE_SECONDS",
    "ConfigFileError",
    "ConfigurationResult",
    "EvaluationError",
    "Label",
    "LabelFileError",
    "LabelSet",
    "Match",
    "MatchResult",
    "Metrics",
    "NamedConfig",
    "Observation",
    "ReplayError",
    "ReplayResult",
    "baseline",
    "compare_configurations",
    "compute_metrics",
    "evaluate_configuration",
    "load_config_file",
    "load_labels",
    "load_session_samples",
    "match_candidates",
    "named_config",
    "rank",
    "replay_samples",
    "replay_session",
]
