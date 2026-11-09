"""Contract tests only: no engines, target execution, network or real REA backend."""

import copy
import io
import json
import unittest
from unittest.mock import patch

from southbound import mcp_server as server
from southbound.analysis import AnalysisBackend, AnalysisError, AnalysisResult
from southbound.analysis.contracts import ERROR_CODES, OPERATIONS, TOOLS, validate


class FakeBackend(AnalysisBackend):
    def __init__(self):
        self.requests = []
        self.closed = False
        self.stopped = False
        self.failure = None
        self.result = None

    def dispatch(self, operation, request):
        self.requests.append((operation, copy.deepcopy(request)))
        if self.failure:
            raise self.failure
        if self.result is not None:
            return self.result
        if operation != "open_target":
            if request["session_id"] != "s1":
                raise AnalysisError("SESSION_NOT_FOUND", "Unknown session.")
            if request["target_id"] != "t1":
                raise AnalysisError("TARGET_NOT_FOUND", "Unknown target.")
            if self.closed and operation not in ("close_target", "analysis_status"):
                raise AnalysisError("TARGET_NOT_FOUND", "Target is closed.")
        function = {"address": "0x1000", "name": "main"}
        data = {
            "open_target": {"path": "/samples/demo", "state": "ready"},
            "target_overview": {"format": "ELF", "architecture": "x86_64", "function_count": 1},
            "search_functions": {"items": [function]},
            "search_strings": {"items": [{"address": "0x2000", "value": "demo"}]},
            "analyze_function": {"function": function, "pseudocode": "return 0;", "calls": []},
            "analysis_status": {"state": "closed" if self.closed else "ready"},
            "close_target": {"closed": True},
        }[operation]
        if operation == "close_target":
            self.closed = True
        return AnalysisResult("s1", "t1", data, [
            {"source": "test-fixture", "operation": operation, "locator": "fixture:demo"},
        ], "complete")

    def open_target(self, request):
        return self.dispatch("open_target", request)

    def target_overview(self, request):
        return self.dispatch("target_overview", request)

    def search_functions(self, request):
        return self.dispatch("search_functions", request)

    def search_strings(self, request):
        return self.dispatch("search_strings", request)

    def analyze_function(self, request):
        return self.dispatch("analyze_function", request)

    def analysis_status(self, request):
        return self.dispatch("analysis_status", request)

    def close_target(self, request):
        return self.dispatch("close_target", request)

    def shutdown(self):
        self.stopped = True


def arguments(operation):
    if operation == "open_target":
        return {"path": "/samples/demo"}
    result = {"session_id": "s1", "target_id": "t1"}
    if operation.startswith("search_"):
        result["query"] = "ma"
    if operation == "analyze_function":
        result["selector"] = {"name": "main"}
    return result


class AnalysisMcpTest(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        server.configure_analysis_backend(self.backend)
        self.addCleanup(server.configure_analysis_backend)

    def call(self, operation, args=None):
        response = server.handle({"jsonrpc": "2.0", "id": 42, "method": "tools/call",
                                  "params": {"name": "decompile_" + operation,
                                             "arguments": arguments(operation) if args is None else args}})
        self.assertEqual(response["id"], 42)
        result = response["result"]
        payload = result["structuredContent"]
        self.assertEqual(json.loads(result["content"][0]["text"]), payload)
        self.assertEqual(result["isError"], not payload["ok"])
        schema = next(t["outputSchema"] for t in TOOLS if t["name"] == "decompile_" + operation)
        validate(payload, schema)
        return payload

    def test_catalog_and_all_operations(self):
        tools = server.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(tools), 22)
        self.assertEqual(len({t["name"] for t in tools}), 22)
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                self.assertTrue(self.call(operation)["ok"])
                self.assertEqual(self.backend.requests[-1][0], operation)
        self.assertEqual(self.backend.requests[2][1]["limit"], 50)

    def test_invalid_input_never_reaches_backend(self):
        cases = [
            ("open_target", {}), ("open_target", {"path": "relative"}),
            ("open_target", {"path": "/demo", "execute": True}),
            ("open_target", []), ("open_target", False),
            ("open_target", {"path": "  "}),
            ("search_functions", {**arguments("search_functions"), "limit": True}),
            ("search_strings", {**arguments("search_strings"), "limit": 201}),
            ("search_strings", {**arguments("search_strings"), "limit": 0}),
            ("search_strings", {**arguments("search_strings"), "cursor": ""}),
            ("search_strings", {**arguments("search_strings"), "query": ""}),
            ("analyze_function", {**arguments("analyze_function"), "selector": {}}),
            ("analyze_function", {**arguments("analyze_function"),
                                  "selector": {"name": "main", "address": "0x1000"}}),
            ("analyze_function", {**arguments("analyze_function"), "selector": {"address": 4096}}),
            ("analyze_function", {**arguments("analyze_function"), "selector": {"address": "1000"}}),
            ("analysis_status", {"session_id": "s1"}),
        ]
        for operation, args in cases:
            with self.subTest(operation=operation, args=args):
                self.assertEqual(self.call(operation, args)["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.backend.requests, [])
        response = server.handle({"id": 1, "method": "tools/call", "params": {
            "name": "decompile_open_target", "arguments": None}})
        self.assertEqual(response["result"]["structuredContent"]["error"]["code"], "INVALID_ARGUMENT")

    def test_default_is_unavailable_for_every_operation(self):
        server.configure_analysis_backend()
        for operation in OPERATIONS:
            result = self.call(operation)
            self.assertEqual(result["error"]["code"], "BACKEND_UNAVAILABLE")
            self.assertNotIn("data", result)

    def test_expected_and_unexpected_errors(self):
        for code in ERROR_CODES:
            self.backend.failure = AnalysisError(code, "Safe message.", True)
            error = self.call("target_overview")["error"]
            self.assertEqual(error, {"code": code, "message": "Safe message.", "retryable": True})
        self.backend.failure = RuntimeError("secret-engine-output")
        result = self.call("target_overview")
        self.assertEqual(result["error"]["code"], "BACKEND_ERROR")
        self.assertNotIn("secret-engine-output", json.dumps(result))

    def test_function_address_selection_and_input_copy(self):
        args = {**arguments("analyze_function"), "selector": {"address": "0x1000"}}
        self.assertTrue(self.call("analyze_function", args)["ok"])
        self.assertEqual(self.backend.requests[-1][1], args)
        args = arguments("search_functions")
        self.call("search_functions", args)
        self.assertNotIn("limit", args)

    def test_partial_results_and_pagination(self):
        self.backend.result = AnalysisResult("s1", "t1", {
            "items": [{"address": "0x1000"}], "next_cursor": "page-2",
        }, [{"source": "fixture", "operation": "search", "locator": "fixture:page1"}],
            "partial", ["More matches are available."])
        result = self.call("search_functions")
        self.assertTrue(result["ok"])
        self.assertEqual(result["completeness"], "partial")
        self.assertEqual(result["data"]["next_cursor"], "page-2")
        self.backend.result.completeness = "unknown"
        self.backend.result.data.pop("next_cursor")
        self.assertTrue(self.call("search_functions")["ok"])
        self.call("search_functions", {**arguments("search_functions"), "cursor": "page-2", "limit": 1})
        self.assertEqual(self.backend.requests[-1][1]["cursor"], "page-2")

    def test_bad_backend_results_are_rejected(self):
        good = self.backend.dispatch("analyze_function", arguments("analyze_function"))
        cases = [None, {}, "not a result"]
        for field, value in [
            ("session_id", "wrong"), ("target_id", "wrong"),
            ("data", {"function": {"address": "invalid"}}),
            ("data", {"function": {"address": "0x1000"}}),
            ("data", {"function": {"address": "0x1000"}, "pseudocode": 123}),
            ("data", {"function": {"address": "0x1000"}, "extra": True}),
            ("evidence", []), ("evidence", [{"source": "missing-fields"}]),
            ("completeness", "partial"), ("completeness", "unknown"),
        ]:
            result = copy.deepcopy(good)
            setattr(result, field, value)
            cases.append(result)
        # None has special meaning in the fixture, so use a mock for malformed results.
        for result in cases:
            with self.subTest(result=result), patch.object(self.backend, "analyze_function", return_value=result):
                self.assertEqual(self.call("analyze_function")["error"]["code"], "BACKEND_CONTRACT_ERROR")
        self.backend.result = AnalysisResult("s1", "t1", {"items": [], "next_cursor": "next"},
                                            good.evidence, "complete")
        self.assertEqual(self.call("search_functions")["error"]["code"], "BACKEND_CONTRACT_ERROR")
        self.backend.result.data = {"items": [{"address": "0x1"}, {"address": "0x2"}]}
        result = self.call("search_functions", {**arguments("search_functions"), "limit": 1})
        self.assertEqual(result["error"]["code"], "BACKEND_CONTRACT_ERROR")

    def test_scope_errors_and_idempotent_close(self):
        for field, code in [("session_id", "SESSION_NOT_FOUND"), ("target_id", "TARGET_NOT_FOUND")]:
            self.assertEqual(self.call("target_overview", {**arguments("target_overview"), field: "missing"})
                             ["error"]["code"], code)
        self.assertTrue(self.call("close_target")["ok"])
        self.assertTrue(self.call("close_target")["ok"])
        self.assertEqual(self.call("analysis_status")["data"]["state"], "closed")
        self.assertEqual(self.call("target_overview")["error"]["code"], "TARGET_NOT_FOUND")

    def test_backend_ownership_and_shutdown_on_eof(self):
        server.configure_analysis_backend(self.backend)
        self.assertFalse(self.backend.stopped)
        with patch("sys.stdin", io.StringIO("")), patch.object(server.RUNTIME, "close") as close:
            server.main()
        self.assertTrue(self.backend.stopped)
        close.assert_called_once()

    def test_cleanup_failure_keeps_backend_and_still_closes_iot(self):
        replacement = FakeBackend()
        with patch.object(self.backend, "shutdown", side_effect=RuntimeError("cleanup failed")):
            with self.assertRaises(RuntimeError):
                server.configure_analysis_backend(replacement)
            self.assertIs(server.ANALYSIS.backend, self.backend)
            with patch("sys.stdin", io.StringIO("")), patch.object(server.RUNTIME, "close") as close:
                with self.assertRaises(RuntimeError):
                    server.main()
                close.assert_called_once()
        self.assertFalse(replacement.stopped)
        server.configure_analysis_backend(replacement)
        self.assertTrue(self.backend.stopped)

    def test_existing_mcp_behaviour(self):
        result = server.handle({"id": 1, "method": "tools/call", "params": {"name": "sim_devices"}})
        self.assertFalse(result["result"]["isError"])
        self.assertNotIn("structuredContent", result["result"])
        self.assertEqual(server.handle({"id": 2, "method": "ping"})["result"], {})
        unknown = server.handle({"id": 3, "method": "tools/call", "params": {"name": "missing"}})
        self.assertEqual(unknown["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
