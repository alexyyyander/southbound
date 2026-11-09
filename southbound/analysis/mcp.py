"""Translate the analysis port into MCP results, enforcing both sides of the contract."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from .backend import AnalysisBackend, AnalysisError, AnalysisResult, UnavailableBackend
from .contracts import OPERATIONS, success_schema, validate


class AnalysisTools:
    def __init__(self, backend: AnalysisBackend | None = None) -> None:
        self.backend = backend if backend is not None else UnavailableBackend()

    def call(self, operation: str, arguments: Any) -> dict:
        try:
            if operation not in OPERATIONS:
                raise AnalysisError("INVALID_ARGUMENT", "Unknown analysis operation.")
            _, inputs, outputs = OPERATIONS[operation]
            try:
                validate(arguments, inputs)
            except ValueError as exc:
                raise AnalysisError("INVALID_ARGUMENT", str(exc)) from exc
            # JSON copy isolates caller-owned nested objects; defaults stay out of the wire input.
            request = json.loads(json.dumps(arguments))
            if operation.startswith("search_"):
                request.setdefault("limit", 50)
            result = getattr(self.backend, operation)(request)
            try:
                if not isinstance(result, AnalysisResult):
                    raise ValueError("expected AnalysisResult")
                payload = {"ok": True, **asdict(result)}
                validate(payload, success_schema(outputs))
                if operation != "open_target" and any(
                    payload[key] != arguments[key] for key in ("session_id", "target_id")
                ):
                    raise ValueError("backend returned a different target/session")
                if result.completeness != "complete" and not result.limitations:
                    raise ValueError("incomplete result requires limitations")
                if result.completeness == "complete":
                    if operation == "target_overview" and not result.data:
                        raise ValueError("empty overview cannot be complete")
                    if operation == "analyze_function" and not any(
                        result.data.get(key) for key in ("pseudocode", "assembly")
                    ):
                        raise ValueError("missing function code cannot be complete")
                if result.data.get("next_cursor") and result.completeness != "partial":
                    raise ValueError("paginated result must be partial")
                if operation not in ("analysis_status", "close_target") and not result.evidence:
                    raise ValueError("observed analysis requires evidence")
                if operation.startswith("search_") and len(result.data["items"]) > request["limit"]:
                    raise ValueError("search result exceeds requested limit")
            except (ValueError, TypeError) as exc:
                raise AnalysisError(
                    "BACKEND_CONTRACT_ERROR", "Analysis backend returned an invalid result."
                ) from exc
        except AnalysisError as exc:
            payload = {"ok": False, "error": {
                "code": exc.code, "message": str(exc), "retryable": exc.retryable,
            }}
        except Exception:
            # Unexpected backend exceptions may contain paths, credentials or raw engine output.
            payload = {"ok": False, "error": {
                "code": "BACKEND_ERROR", "message": "Analysis backend operation failed.",
                "retryable": False,
            }}
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload, "isError": not payload["ok"],
        }

    def shutdown(self) -> None:
        self.backend.shutdown()
