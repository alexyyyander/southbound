"""Tiny REA-shaped MCP peer, exclusively for subprocess contract tests."""
import hashlib
import json
from pathlib import Path
import sys
import time


def reply(name, args):
    if name == "open_binary":
        return {"result": {"path": args["path"], "sha256": hashlib.sha256(
            Path(args["path"]).read_bytes()).hexdigest(), "format": "elf", "architecture": "x86_64"}}
    if name == "close_binary":
        return {"result": None}
    if name == "binary_session":
        return {"result": {"open": True, "target": {"path": "fixture"}}}
    if args.get("pattern") == "timeout":
        time.sleep(10)
    functions = [{"address": "0x1000", "value": "main"},
                 {"address": "0x2000", "value": "main_helper"}]
    values = {
        "binary_overview": {"procedure_count": 2, "string_count": 1},
        "search_procedures": functions,
        "search_strings": [{"address": "0x3000", "value": "hello"}],
        "analyze_function": {"procedure": {"address": "0x1000", "name": "main"},
                             "pseudocode": "return 7;", "assembly": ["ret"], "callees": [],
                             "facet_availability": {"callees": {"available": True}},
                             "limitations": []},
    }
    return {"evidence_id": "ev_fixture", "normalized_result": values[name],
            "provider": {"name": "test-peer"}, "limitations": []}


for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    if request["method"] == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "test-peer", "version": "fixture"}}
    else:
        pattern = request["params"]["arguments"].get("pattern")
        if pattern == "broken-json":
            print("not json", flush=True)
            continue
        if pattern == "error":
            result = {"isError": True, "structuredContent": {"error": {
                "code": "provider_unavailable", "category": "unavailable", "message": "private-path"}}}
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
            continue
        data = reply(request["params"]["name"], request["params"]["arguments"])
        result = {"structuredContent": data, "content": []}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
