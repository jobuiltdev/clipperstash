"""Errors the evaluation layer raises.

All of them mean "the operator gave us something we cannot evaluate". None of
them indicate a detector failure, and none carry chat content.
"""

from __future__ import annotations


class EvaluationError(Exception):
    """Something about the evaluation inputs is unusable."""


class LabelFileError(EvaluationError):
    """A ground-truth label file could not be read or does not validate."""


class ConfigFileError(EvaluationError):
    """An experimental detector configuration file is invalid."""


class ReplayError(EvaluationError):
    """Replay was asked for something it cannot do."""
