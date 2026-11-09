"""Opt-in real MCP -> REA -> Ghidra smoke; never executes the compiled input."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from southbound.analysis.rea_transport import ReaTransport


@unittest.skipUnless(os.environ.get("SOUTHBOUND_REA_LIVE_CONFIG"), "real REA backend not requested")
class ReaLiveTest(unittest.TestCase):
    def test_real_mcp_lifecycle(self):
        config_path = os.environ["SOUTHBOUND_REA_LIVE_CONFIG"]
        config = json.loads(Path(config_path).read_text())
        with tempfile.TemporaryDirectory(dir=config["allowed_roots"][0]) as folder:
            source = Path(folder) / "sample.c"
            binary = Path(folder) / "sample"
            source.write_text('#include <stdio.h>\n'
                              '__attribute__((noinline)) int southbound_demo(int x) '
                              '{return x*3+7;}\n'
                              'int main(void){puts("SOUTHBOUND_REA_SMOKE");'
                              'return southbound_demo(5);}\n')
            subprocess.run(["cc", "-O0", "-g", str(source), "-o", str(binary)],
                           check=True, capture_output=True, timeout=60)
            peer = ReaTransport([sys.executable, "-B", "-m", "southbound.mcp_server"],
                                {"SOUTHBOUND_ANALYSIS_CONFIG": config_path}, 300)
            trace = []
            try:
                peer.start()

                def call(name, args):
                    result = peer.request("tools/call", {"name": name, "arguments": args})
                    trace.append({"tool": name, "result": result})
                    self.assertFalse(result.get("isError"), result)
                    return result["structuredContent"]

                opened = call("decompile_open_target", {"path": str(binary)})
                scope = {k: opened[k] for k in ("session_id", "target_id")}
                overview = call("decompile_target_overview", scope)
                self.assertGreater(overview["data"]["function_count"], 0)
                functions = call("decompile_search_functions", {**scope, "query": "southbound_demo"})
                self.assertTrue(functions["data"]["items"])
                strings = call("decompile_search_strings", {**scope, "query": "SOUTHBOUND_REA_SMOKE"})
                self.assertTrue(strings["data"]["items"])
                analyzed = call("decompile_analyze_function", {**scope, "selector": {
                    "address": functions["data"]["items"][0]["address"]}})
                self.assertTrue(analyzed["data"].get("pseudocode"))
                self.assertIn("7", analyzed["data"]["pseudocode"])
                saved = json.loads(Path(analyzed["evidence"][0]["locator"]).read_text())
                self.assertEqual(saved["provider"]["id"], "ghidra")
                if functions["data"]["items"][0].get("name"):
                    call("decompile_analyze_function", {**scope, "selector": {
                        "name": functions["data"]["items"][0]["name"]}})
                self.assertTrue(Path(analyzed["evidence"][0]["locator"]).is_file())
                status = call("decompile_analysis_status", scope)
                self.assertEqual(status["data"]["state"], "ready")
                call("decompile_close_target", scope)
                call("decompile_close_target", scope)
            finally:
                peer.close()
                report = Path(config["state_dir"]) / "live-smoke.json"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps(trace, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main()
