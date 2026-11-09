"""REA MCP adapter. Each target owns a separate server and immutable input copy."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any
import uuid

from .backend import AnalysisBackend, AnalysisError, AnalysisResult
from .rea_transport import ReaTransport


@dataclass
class Target:
    session_id: str
    target_id: str
    path: str
    sha256: str
    workspace: Path
    transport: ReaTransport
    summary: dict = field(default_factory=dict)
    state: str = "ready"
    pages: dict = field(default_factory=dict)


class ReaBackend(AnalysisBackend):
    def __init__(self, command: list[str], allowed_roots: list[str], state_dir: str,
                 env: dict[str, str] | None = None, timeout: float = 180,
                 provider_id: str = "ghidra") -> None:
        if (not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command)
                or not Path(command[0]).is_absolute() or not isinstance(allowed_roots, list)
                or not allowed_roots or not all(isinstance(p, str) and Path(p).is_absolute()
                                                for p in allowed_roots)
                or type(timeout) not in (int, float) or not 1 <= timeout <= 600
                or not isinstance(provider_id, str) or not provider_id):
            raise ValueError("invalid REA configuration")
        self.command = command
        self.roots = [Path(p).resolve(strict=True) for p in allowed_roots]
        if not all(p.is_dir() for p in self.roots):
            raise ValueError("allowed roots must be directories")
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.env = env or {}
        self.timeout = timeout
        self.provider_id = provider_id
        self.targets: dict[str, Target] = {}

    @classmethod
    def from_file(cls, path: str) -> ReaBackend:
        """Load trusted operator configuration, never an MCP request parameter."""
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = {"command", "allowed_roots", "state_dir", "env", "timeout", "provider_id"}
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError("unknown backend configuration fields")
        if not all(Path(config[k]).is_absolute() for k in ("state_dir",)):
            raise ValueError("state_dir must be absolute")
        if not all(isinstance(p, str) and Path(p).is_absolute() for p in config["allowed_roots"]):
            raise ValueError("allowed_roots must be absolute")
        if not isinstance(config.get("env", {}), dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in config.get("env", {}).items()
        ):
            raise ValueError("env must map strings to strings")
        return cls(**config)

    def _target(self, request: dict, allow_closed: bool = False) -> Target:
        matches = [t for t in self.targets.values() if t.session_id == request["session_id"]]
        if not matches:
            raise AnalysisError("SESSION_NOT_FOUND", "Unknown analysis session.")
        target = self.targets.get(request["target_id"])
        if target not in matches or (target.state == "closed" and not allow_closed):
            raise AnalysisError("TARGET_NOT_FOUND", "Unknown or closed target.")
        if target.state == "failed" and not allow_closed:
            raise AnalysisError("BACKEND_UNAVAILABLE", "Target backend failed; close and reopen it.")
        return target

    def _call(self, target: Target, name: str, args: dict) -> dict:
        try:
            reply = target.transport.call(name, args)
            self._record(target, name, reply)
            return reply
        except AnalysisError as exc:
            if isinstance(getattr(exc, "receipt", None), dict):
                self._record(target, name + ":error", exc.receipt)
            if target.transport.process is None:
                target.state = "failed"
            raise

    def _record(self, target: Target, operation: str, value: dict) -> dict:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        folder = self.state_dir / "evidence" / target.target_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (digest + ".json")
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
            os.replace(temporary, path)
        provider = value.get("provider", {})
        source = "rea:" + str(target.transport.server.get("version", "unknown"))
        if isinstance(provider, dict):
            source += ":" + str(provider.get("name", provider.get("id", "lifecycle")))
        return {"source": source, "operation": operation, "locator": str(path),
                "sha256": target.sha256}

    def _result(self, target: Target, data: dict, operation: str, raw: dict,
                limitations: list[str] | None = None, completeness: str = "unknown") -> AnalysisResult:
        limits = list(dict.fromkeys([*(raw.get("limitations") or []), *(limitations or [])]))
        if completeness != "complete" and not limits:
            limits = ["Provider analysis does not establish complete recovery of target behavior."]
        return AnalysisResult(target.session_id, target.target_id, data,
                              [self._record(target, operation, raw)], completeness, limits)

    @staticmethod
    def _normalized(raw: dict, expected: type) -> Any:
        result = raw.get("normalized_result")
        if not isinstance(result, expected) or not raw.get("evidence_id"):
            raise AnalysisError("BACKEND_CONTRACT_ERROR", "Unexpected REA evidence shape.")
        return result

    @staticmethod
    def _function(row: dict, name_field: str = "name") -> dict:
        address = row.get("address", "")
        if not isinstance(address, str) or not re.fullmatch(r"(?:0x)?[0-9a-fA-F]+", address):
            raise AnalysisError("UNSUPPORTED", "Non-flat procedure addresses are not supported.")
        result = {"address": "0x" + address.removeprefix("0x")}
        if row.get(name_field):
            result["name"] = row[name_field]
        return result

    def open_target(self, request: dict) -> AnalysisResult:
        if os.name != "posix":
            raise AnalysisError("UNSUPPORTED", "REA stdio integration currently requires POSIX.")
        if len(self.targets) >= 128 or sum(t.state != "closed" for t in self.targets.values()) >= 4:
            raise AnalysisError("BUSY", "Analysis target limit reached; close targets or restart session.")
        try:
            path = Path(request["path"]).resolve(strict=True)
        except FileNotFoundError as exc:
            raise AnalysisError("TARGET_NOT_FOUND", "Target file does not exist.") from exc
        except OSError as exc:
            raise AnalysisError("ACCESS_DENIED", "Target file is not accessible.") from exc
        if not any(path.is_relative_to(root) for root in self.roots):
            raise AnalysisError("ACCESS_DENIED", "Target is outside the allowed analysis directories.")
        workspace = Path(tempfile.mkdtemp(prefix="target-", dir=self.state_dir))
        transport = ReaTransport(self.command, self.env, self.timeout)
        try:
            # Snapshot a regular file without executing it; final component cannot change to a symlink.
            directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in path.parts[1:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                    os.close(directory)
                    directory = child
                fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                             dir_fd=directory)
            finally:
                os.close(directory)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 128 * 1024 * 1024:
                os.close(fd)
                raise AnalysisError("UNSUPPORTED", "Target must be a regular file of at most 128 MiB.")
            with os.fdopen(fd, "rb") as source:
                snapshot = workspace / ("input" + path.suffix)
                with snapshot.open("wb") as output:
                    remaining = 128 * 1024 * 1024 + 1
                    while remaining:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            break
                        output.write(chunk)
                        remaining -= len(chunk)
                    if remaining == 0:
                        raise AnalysisError("UNSUPPORTED", "Target grew beyond the input limit.")
            sha256 = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            target = Target(uuid.uuid4().hex, uuid.uuid4().hex, str(path), sha256, workspace, transport)
            transport.start()
            raw = self._call(target, "open_binary", {"path": str(snapshot), "provider_id": self.provider_id})
            summary = raw.get("result")
            if not isinstance(summary, dict) or summary.get("sha256") != sha256:
                raise AnalysisError("BACKEND_CONTRACT_ERROR", "REA target identity does not match input.")
            target.summary = summary
            result = self._result(target, {"path": str(path), "state": "ready"},
                                  "open_binary", raw, completeness="complete")
            self.targets[target.target_id] = target
            return result
        except Exception as exc:
            transport.close()
            shutil.rmtree(workspace)
            if isinstance(exc, AnalysisError):
                raise
            raise AnalysisError("BACKEND_ERROR", "Cannot open the analysis target.") from exc

    def target_overview(self, request: dict) -> AnalysisResult:
        target = self._target(request)
        raw = self._call(target, "binary_overview", {})
        normalized = self._normalized(raw, dict)
        data = {key: target.summary[key] for key in ("format", "architecture")
                if isinstance(target.summary.get(key), str)}
        for source, dest in (("procedure_count", "function_count"), ("string_count", "string_count")):
            if source in normalized:
                data[dest] = normalized[source]
        return self._result(target, data, "binary_overview", raw,
                            ["Inventory discovery coverage is unknown; counts reflect provider observations."])

    def _search(self, request: dict, operation: str) -> AnalysisResult:
        target = self._target(request)
        cursor = request.get("cursor")
        if cursor:
            page = target.pages.get(cursor)
            if not page or page["query"] != request["query"] or page["operation"] != operation:
                raise AnalysisError("INVALID_ARGUMENT", "Invalid, expired or mismatched search cursor.")
            rows, offset, raw = page["rows"], page["offset"], page["raw"]
        else:
            raw = self._call(target, operation, {"pattern": request["query"], "mode": "literal",
                                                "case_sensitive": False, "detail": "complete"})
            rows = self._normalized(raw, list)
            offset = 0
        selected = rows[offset:offset + request["limit"]]
        if operation == "search_procedures":
            items = [self._function(row, "value") for row in selected]
        else:
            items = [{"address": self._function(row)["address"], "value": row["value"]}
                     for row in selected]
        data = {"items": items}
        more = offset + len(selected) < len(rows)
        if more:
            token = uuid.uuid4().hex
            if len(target.pages) >= 16:
                target.pages.pop(next(iter(target.pages)))
            target.pages[token] = {"rows": rows, "offset": offset + len(selected), "raw": raw,
                                   "query": request["query"], "operation": operation}
            data["next_cursor"] = token
        limits = ["Search covers the provider inventory; undiscovered functions or strings may be absent."]
        if more:
            limits.append("Additional matching rows are available via next_cursor.")
        return self._result(target, data, operation, raw, limits, "partial" if more else "unknown")

    def search_functions(self, request: dict) -> AnalysisResult:
        return self._search(request, "search_procedures")

    def search_strings(self, request: dict) -> AnalysisResult:
        return self._search(request, "search_strings")

    def analyze_function(self, request: dict) -> AnalysisResult:
        target = self._target(request)
        selector = request["selector"]
        if "name" in selector:
            found = self._call(target, "search_procedures", {
                "pattern": selector["name"], "mode": "literal", "case_sensitive": True,
                "detail": "complete",
            })
            rows = self._normalized(found, list)
            matches = {r["address"] for r in rows if r.get("value") == selector["name"]
                       or selector["name"] in r.get("matched_aliases", [])}
            if not matches:
                raise AnalysisError("FUNCTION_NOT_FOUND", "No function has this exact name.")
            if len(matches) != 1:
                raise AnalysisError("AMBIGUOUS_FUNCTION", "Name matches multiple functions; use an address.")
            address = next(iter(matches))
        else:
            address = selector["address"]
        raw = self._call(target, "analyze_function", {"procedure": address, "detail": "complete"})
        dossier = self._normalized(raw, dict)
        data = {"function": self._function(dossier["procedure"])}
        limits = list(dossier.get("limitations", []))
        if dossier.get("pseudocode"):
            data["pseudocode"] = dossier["pseudocode"]
        else:
            limits.append("Pseudocode is unavailable.")
        if dossier.get("assembly"):
            data["assembly"] = "\n".join(dossier["assembly"])
        facets = dossier.get("facet_availability", {})
        if facets.get("callees", {}).get("available") is True:
            data["calls"] = [self._function(row) for row in dossier["callees"]]
        else:
            limits.append("Callee coverage is not declared available; calls omitted.")
        limits.append("Static recovery does not prove full runtime behavior or original source identity.")
        return self._result(target, data, "analyze_function", raw, limits, "partial")

    def analysis_status(self, request: dict) -> AnalysisResult:
        target = self._target(request, allow_closed=True)
        if target.state in ("closed", "failed"):
            return AnalysisResult(target.session_id, target.target_id, {"state": target.state},
                                  completeness="complete")
        raw = self._call(target, "binary_session", {})
        status = raw.get("result")
        if not isinstance(status, dict) or status.get("open") is not True:
            target.state = "failed"
            return self._result(target, {"state": "failed"}, "binary_session", raw,
                                ["REA no longer reports an open target."])
        health = status.get("provider_operation_health") or {}
        activity = status.get("analysis_activity") or {}
        state = "ready"
        if health.get("state") in ("exited", "unreachable"):
            state = "failed"
        elif activity.get("status") == "busy" or health.get("state") == "busy":
            state = "analyzing"
        return self._result(target, {"state": state}, "binary_session", raw,
                            completeness="complete")

    def close_target(self, request: dict) -> AnalysisResult:
        target = self._target(request, allow_closed=True)
        if target.state != "closed":
            try:
                if target.transport.process is not None:
                    self._call(target, "close_binary", {})
            finally:
                target.transport.close()
                if target.workspace.exists():
                    shutil.rmtree(target.workspace)
                target.pages.clear()
                target.state = "closed"
        return AnalysisResult(target.session_id, target.target_id, {"closed": True},
                              completeness="complete")

    def shutdown(self) -> None:
        errors = []
        for target in self.targets.values():
            try:
                self.close_target({"session_id": target.session_id, "target_id": target.target_id})
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise AnalysisError("BACKEND_ERROR", "Some analysis resources could not be released.")
