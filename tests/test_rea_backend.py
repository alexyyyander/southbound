"""Real subprocess transport tests against a controlled peer, never a real decompiler."""

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from southbound.analysis import AnalysisError
from southbound.analysis.mcp import AnalysisTools
from southbound.analysis.rea_backend import ReaBackend
from southbound.analysis.rea_transport import ReaTransport


class ReaBackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.inputs = root / "inputs"
        self.inputs.mkdir()
        self.sample = self.inputs / "sample.bin"
        self.sample.write_bytes(b"test binary bytes")
        self.command = [sys.executable, str(Path(__file__).parent / "fixtures" / "rea_stdio.py")]
        self.backend = ReaBackend(self.command, [str(self.inputs)], str(root / "state"), timeout=1)
        self.addCleanup(self.backend.shutdown)
        self.tools = AnalysisTools(self.backend)

    def call(self, name, args):
        payload = self.tools.call(name, args)["structuredContent"]
        self.assertTrue(payload["ok"], payload)
        return payload

    def open(self):
        result = self.call("open_target", {"path": str(self.sample)})
        return {key: result[key] for key in ("session_id", "target_id")}

    def test_full_lifecycle_and_retained_evidence(self):
        scope = self.open()
        target = self.backend.targets[scope["target_id"]]
        self.sample.write_bytes(b"changed source")
        self.assertEqual((target.workspace / "input.bin").read_bytes(), b"test binary bytes")
        overview = self.call("target_overview", scope)
        self.assertEqual(overview["data"]["function_count"], 2)
        evidence = json.loads(Path(overview["evidence"][0]["locator"]).read_text())
        self.assertEqual(evidence["evidence_id"], "ev_fixture")
        first = self.call("search_functions", {**scope, "query": "main", "limit": 1})
        self.assertEqual(first["completeness"], "partial")
        second = self.call("search_functions", {**scope, "query": "main", "limit": 1,
                                                "cursor": first["data"]["next_cursor"]})
        self.assertEqual(second["data"]["items"][0]["name"], "main_helper")
        self.assertNotIn("next_cursor", second["data"])
        self.assertEqual(self.call("search_strings", {**scope, "query": "hello"})
                         ["data"]["items"][0]["value"], "hello")
        result = self.call("analyze_function", {**scope, "selector": {"name": "main"}})
        self.assertEqual(result["data"]["pseudocode"], "return 7;")
        self.assertEqual(result["data"]["calls"], [])
        self.assertEqual(self.call("analysis_status", scope)["data"]["state"], "ready")
        self.call("close_target", scope)
        self.call("close_target", scope)
        self.assertFalse(target.workspace.exists())
        self.assertIsNone(target.transport.process)
        self.assertTrue(Path(overview["evidence"][0]["locator"]).exists())
        self.assertEqual(self.call("analysis_status", scope)["data"]["state"], "closed")

    def test_path_restrictions_missing_file_and_symlinks(self):
        outside = Path(self.tmp.name) / "outside.bin"
        outside.write_bytes(b"outside")
        link = self.inputs / "outside-link"
        link.symlink_to(outside)
        for path, code in [(outside, "ACCESS_DENIED"), (link, "ACCESS_DENIED"),
                           (self.inputs / "missing", "TARGET_NOT_FOUND"),
                           (self.inputs, "UNSUPPORTED")]:
            payload = self.tools.call("open_target", {"path": str(path)})["structuredContent"]
            self.assertEqual(payload["error"]["code"], code, payload)
        self.assertEqual(list(self.backend.state_dir.glob("target-*")), [])

    def test_cursor_binding_and_invalid_scope(self):
        scope = self.open()
        first = self.call("search_functions", {**scope, "query": "main", "limit": 1})
        for operation, query in [("search_functions", "other"), ("search_strings", "main")]:
            result = self.tools.call(operation, {**scope, "query": query,
                                                "cursor": first["data"]["next_cursor"]})
            self.assertEqual(result["structuredContent"]["error"]["code"], "INVALID_ARGUMENT")
        other = self.open()
        result = self.tools.call("analysis_status", {**scope, "target_id": other["target_id"]})
        self.assertEqual(result["structuredContent"]["error"]["code"], "TARGET_NOT_FOUND")

    def test_timeout_kills_process_and_close_removes_workspace(self):
        scope = self.open()
        result = self.tools.call("search_functions", {**scope, "query": "timeout"})
        self.assertEqual(result["structuredContent"]["error"]["code"], "TIMEOUT")
        self.assertEqual(self.call("analysis_status", scope)["data"]["state"], "failed")
        self.call("close_target", scope)
        self.assertEqual(list(self.backend.state_dir.glob("target-*")), [])

    def test_missing_executable_never_falls_back(self):
        self.backend.command = ["/nonexistent/rea"]
        result = self.tools.call("open_target", {"path": str(self.sample)})
        self.assertEqual(result["structuredContent"]["error"]["code"], "BACKEND_UNAVAILABLE")
        self.assertEqual(list(self.backend.state_dir.glob("target-*")), [])

    def test_function_name_is_exact(self):
        scope = self.open()
        result = self.tools.call("analyze_function", {**scope, "selector": {"name": "mai"}})
        self.assertEqual(result["structuredContent"]["error"]["code"], "FUNCTION_NOT_FOUND")

    def test_upstream_error_mapping_and_diagnostic_receipt(self):
        scope = self.open()
        result = self.tools.call("search_functions", {**scope, "query": "error"})
        self.assertEqual(result["structuredContent"]["error"]["code"], "BACKEND_UNAVAILABLE")
        self.assertNotIn("private-path", json.dumps(result))
        records = list((self.backend.state_dir / "evidence" / scope["target_id"]).glob("*.json"))
        self.assertTrue(any("private-path" in p.read_text() for p in records))
        self.call("close_target", scope)

    def test_bad_json_disconnects_target(self):
        scope = self.open()
        result = self.tools.call("search_functions", {**scope, "query": "broken-json"})
        self.assertEqual(result["structuredContent"]["error"]["code"], "BACKEND_ERROR")
        self.assertEqual(self.call("analysis_status", scope)["data"]["state"], "failed")
        self.call("close_target", scope)

    def test_output_budget_disconnects_process(self):
        transport = ReaTransport(self.command, {}, 1)
        transport.start()
        try:
            transport.MAX_FRAME = 32
            with self.assertRaises(AnalysisError) as caught:
                transport.call("binary_session", {})
            self.assertEqual(caught.exception.code, "BACKEND_CONTRACT_ERROR")
            self.assertIsNone(transport.process)
        finally:
            transport.close()

    def test_main_loads_operator_config_and_releases_on_eof(self):
        from southbound import mcp_server as server
        config = Path(self.tmp.name) / "config.json"
        config.write_text(json.dumps({"command": self.command, "allowed_roots": [str(self.inputs)],
                                      "state_dir": str(Path(self.tmp.name) / "main-state")}))
        request = {"id": 1, "method": "tools/call", "params": {
            "name": "decompile_open_target", "arguments": {"path": str(self.sample)}}}
        stdout = io.StringIO()
        with patch.object(server, "ANALYSIS", AnalysisTools()), patch.object(server.RUNTIME, "close"), \
                patch.dict(os.environ, {"SOUTHBOUND_ANALYSIS_CONFIG": str(config)}), \
                patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")), patch("sys.stdout", stdout):
            server.main()
            self.assertIsInstance(server.ANALYSIS.backend, ReaBackend)
            self.assertTrue(all(t.state == "closed" for t in server.ANALYSIS.backend.targets.values()))
        self.assertTrue(json.loads(stdout.getvalue())["result"]["structuredContent"]["ok"])

    def test_invalid_operator_config_is_unavailable(self):
        from southbound import mcp_server as server
        config = Path(self.tmp.name) / "invalid.json"
        config.write_text('{"command":true}')
        stdout, stderr = io.StringIO(), io.StringIO()
        request = {"id": 1, "method": "tools/call", "params": {
            "name": "decompile_open_target", "arguments": {"path": str(self.sample)}}}
        with patch.object(server, "ANALYSIS", AnalysisTools()), patch.object(server.RUNTIME, "close"), \
                patch.dict(os.environ, {"SOUTHBOUND_ANALYSIS_CONFIG": str(config)}), \
                patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")), \
                patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            server.main()
        self.assertEqual(json.loads(stdout.getvalue())["result"]["structuredContent"]
                         ["error"]["code"], "BACKEND_UNAVAILABLE")
        self.assertIn("invalid analysis backend configuration", stderr.getvalue())

    def test_status_maps_upstream_activity_and_health(self):
        scope = self.open()
        target = self.backend.targets[scope["target_id"]]
        for upstream, expected in [({"analysis_activity": {"status": "busy"}}, "analyzing"),
                                   ({"provider_operation_health": {"state": "unreachable"}}, "failed")]:
            with patch.object(target.transport, "call", return_value={"result": {"open": True, **upstream}}):
                self.assertEqual(self.call("analysis_status", scope)["data"]["state"], expected)

    def test_config_and_initialization(self):
        config = Path(self.tmp.name) / "config.json"
        config.write_text(json.dumps({"command": self.command, "allowed_roots": [str(self.inputs)],
                                      "state_dir": str(Path(self.tmp.name) / "configured")}))
        backend = ReaBackend.from_file(str(config))
        backend.shutdown()
        config.write_text('{"unknown":true}')
        with self.assertRaises(ValueError):
            ReaBackend.from_file(str(config))
        with self.assertRaises(ValueError):
            ReaBackend(["relative"], [str(self.inputs)], str(self.inputs))
        transport = ReaTransport(self.command, {}, 1)
        transport.start()
        try:
            self.assertEqual(transport.server["name"], "test-peer")
        finally:
            transport.close()


if __name__ == "__main__":
    unittest.main()
