"""Small owned MCP stdio client for the REA backend (POSIX hosts)."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from typing import Any

from .backend import AnalysisError


class ReaTransport:
    MAX_FRAME = 16 * 1024 * 1024

    def __init__(self, command: list[str], env: dict[str, str], timeout: float) -> None:
        self.command = command
        self.env = env
        self.timeout = timeout
        self.process: subprocess.Popen | None = None
        self.buffer = b""
        self.sequence = 0
        self.server: dict[str, Any] = {}

    def start(self) -> None:
        if os.name != "posix":
            raise AnalysisError("UNSUPPORTED", "REA stdio integration currently requires POSIX.")
        try:
            self.process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env={**os.environ, **self.env},
                start_new_session=True,
            )
            response = self.request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "southbound-analysis", "version": "1"},
            })
            self.server = response["serverInfo"]
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception as exc:
            self.close()
            if isinstance(exc, AnalysisError):
                raise
            raise AnalysisError("BACKEND_UNAVAILABLE", "Cannot start the configured REA server.") from exc

    def _send(self, message: dict) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        if self.process is None or self.process.poll() is not None:
            raise AnalysisError("BACKEND_UNAVAILABLE", "REA process is not running.")
        self.sequence += 1
        request_id = self.sequence
        deadline = time.monotonic() + self.timeout
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                while True:
                    if time.monotonic() >= deadline:
                        raise AnalysisError("TIMEOUT", "REA request exceeded its time budget.")
                    if b"\n" in self.buffer:
                        line, self.buffer = self.buffer.split(b"\n", 1)
                        message = json.loads(line)
                        if "method" in message and "id" in message:
                            self._send({"jsonrpc": "2.0", "id": message["id"], "error": {
                                "code": -32601, "message": "Client requests are not supported.",
                            }})
                            continue
                        if message.get("id") != request_id:
                            continue
                        if "error" in message:
                            raise AnalysisError("BACKEND_ERROR", "REA rejected the MCP request.")
                        if not isinstance(message.get("result"), dict):
                            raise ValueError("missing MCP result")
                        return message["result"]
                    if not selector.select(max(0, deadline - time.monotonic())):
                        raise AnalysisError("TIMEOUT", "REA request exceeded its time budget.")
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                    if not chunk:
                        raise AnalysisError("BACKEND_UNAVAILABLE", "REA process disconnected.")
                    self.buffer += chunk
                    if len(self.buffer) > self.MAX_FRAME:
                        raise AnalysisError("BACKEND_CONTRACT_ERROR", "REA response exceeds 16 MiB.")
        except Exception as exc:
            self.close()
            if isinstance(exc, AnalysisError):
                raise
            raise AnalysisError("BACKEND_ERROR", "REA transport failed.") from exc

    def call(self, name: str, arguments: dict) -> dict:
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        data = reply.get("structuredContent")
        if not isinstance(data, dict):
            try:
                data = json.loads(next(c["text"] for c in reply.get("content", [])
                                       if c.get("type") == "text"))
            except (ValueError, StopIteration, KeyError, TypeError) as exc:
                raise AnalysisError("BACKEND_CONTRACT_ERROR", "REA did not return structured JSON.") from exc
        if reply.get("isError"):
            error = data.get("error", {})
            code = str(error.get("code", "")).lower() if isinstance(error, dict) else ""
            mapping = {
                "provider_timeout": "TIMEOUT", "cancelled": "CANCELLED",
                "invalid_request": "INVALID_ARGUMENT", "access_denied": "ACCESS_DENIED",
                "capability_unavailable": "UNSUPPORTED", "resource_constraint": "BUSY",
                "provider_unavailable": "BACKEND_UNAVAILABLE", "unsupported_target": "UNSUPPORTED",
                "analysis_access_denied": "ACCESS_DENIED",
            }
            category = error.get("category", "") if isinstance(error, dict) else ""
            categories = {"unavailable": "BACKEND_UNAVAILABLE", "timeout": "TIMEOUT",
                          "cancelled": "CANCELLED", "unsupported_target": "UNSUPPORTED",
                          "unsupported_provider": "UNSUPPORTED", "invalid_input": "INVALID_ARGUMENT"}
            failure = AnalysisError(mapping.get(code, categories.get(category, "BACKEND_ERROR")),
                                    "REA analysis failed; check the configured engine and target.")
            failure.receipt = data
            raise failure
        return data

    def close(self) -> None:
        process, self.process = self.process, None
        self.buffer = b""
        if process is None:
            return
        if process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        # EOF is the normal MCP shutdown: let the server release its engines first.
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        # Clean any members still in this owned process group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)
        if process.stdout:
            process.stdout.close()
