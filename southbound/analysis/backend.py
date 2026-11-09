"""Backend port. No REA imports, subprocesses, filesystem access or scheduler."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .contracts import ERROR_CODES


@dataclass
class AnalysisResult:
    """Backend response; validated against the operation's wire schema before delivery."""

    session_id: str
    target_id: str
    data: dict[str, Any]
    evidence: list[dict[str, str]] = field(default_factory=list)
    completeness: str = "unknown"
    limitations: list[str] = field(default_factory=list)


class AnalysisError(Exception):
    """Expected backend failure. Message must be safe for remote clients."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        if code not in ERROR_CODES or not isinstance(message, str) or not message.strip():
            raise ValueError("invalid analysis error")
        if type(retryable) is not bool:
            raise ValueError("retryable must be boolean")
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AnalysisBackend(ABC):
    """Synchronous, bounded calls. Backend owns identities, state and resource cleanup.

    Requests are validated dictionaries; results follow contracts.OPERATIONS.
    Exact-name ambiguity must raise AMBIGUOUS_FUNCTION, never select arbitrarily.
    close_target is idempotent for a previously closed target in the same session.
    shutdown must safely release all remaining resources, including failed opens.
    """

    @abstractmethod
    def open_target(self, request: dict[str, Any]) -> AnalysisResult: ...

    @abstractmethod
    def target_overview(self, request: dict[str, Any]) -> AnalysisResult: ...

    @abstractmethod
    def search_functions(self, request: dict[str, Any]) -> AnalysisResult: ...

    @abstractmethod
    def search_strings(self, request: dict[str, Any]) -> AnalysisResult: ...

    @abstractmethod
    def analyze_function(self, request: dict[str, Any]) -> AnalysisResult: ...

    @abstractmethod
    def analysis_status(self, request: dict[str, Any]) -> AnalysisResult: ...

    @abstractmethod
    def close_target(self, request: dict[str, Any]) -> AnalysisResult: ...

    @abstractmethod
    def shutdown(self) -> None: ...


class UnavailableBackend(AnalysisBackend):
    """Production default: never fabricates targets, evidence or analysis."""

    def _unavailable(self, request: dict[str, Any]) -> AnalysisResult:
        raise AnalysisError("BACKEND_UNAVAILABLE", "No analysis backend is configured.")

    open_target = _unavailable
    target_overview = _unavailable
    search_functions = _unavailable
    search_strings = _unavailable
    analyze_function = _unavailable
    analysis_status = _unavailable
    close_target = _unavailable

    def shutdown(self) -> None:
        pass
