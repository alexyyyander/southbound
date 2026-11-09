"""Independent static-analysis interface; backends are supplied by integrations."""

from .backend import AnalysisBackend, AnalysisError, AnalysisResult

__all__ = ["AnalysisBackend", "AnalysisError", "AnalysisResult"]
