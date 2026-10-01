#!/usr/bin/env python3
"""Tiny client for the 2026-07-28 "stateless" MCP wire format, used to drive
the demo against agentgateway. Not a general-purpose MCP SDK: it hardcodes
the exact headers and _meta field agentgateway v1.5.0 requires for a modern,
session-less request, because reproducing those requirements exactly is the
point of this repo.

Confirmed empirically against agentgateway v1.5.0 (statefulMode: stateless):
  - HTTP header `MCP-Protocol-Version: 2026-07-28` is required or agentgateway
    returns 400 "invalid MCP protocol version header".
  - params._meta["io.modelcontextprotocol/protocolVersion"] must also be set,
    or agentgateway returns 400 "_meta.protocolVersion is required for modern
    requests".
  - HTTP headers `Mcp-Method` and `Mcp-Name` (tool calls only) are required
    for agentgateway's own routing, or it returns 400 "invalid MCP routing
    header: Mcp-Method".
  - No `Mcp-Session-Id` header is sent, ever. None of the three calls above
    needed one.
"""

from __future__ import annotations

import json
import re
import urllib.request

PROTOCOL_VERSION = "2026-07-28"


def call(base_url: str, method: str, name: str | None, arguments: dict | None, req_id: int = 1) -> dict:
    params = {"_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION}}
    if name is not None:
        params["name"] = name
    if arguments is not None:
        params["arguments"] = arguments

    body = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name

    req = urllib.request.Request(base_url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode()

    # Responses may be plain JSON or a one-event SSE stream ("event: message\ndata: {...}").
    m = re.search(r"data:\s*(\{.*\})", raw, re.S)
    payload = m.group(1) if m else raw
    return json.loads(payload)


def tool_result(base_url: str, name: str, arguments: dict, req_id: int = 1) -> dict:
    resp = call(base_url, "tools/call", name, arguments, req_id)
    if "error" in resp:
        raise RuntimeError(f"{name} failed: {resp['error']}")
    return resp["result"]["structuredContent"]
