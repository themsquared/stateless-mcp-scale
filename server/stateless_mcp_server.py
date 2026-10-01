#!/usr/bin/env python3
"""A minimal MCP server written to the 2026-07-28 "stateless" spec revision.

This is a reference implementation for a blog demo, not a production SDK. It
exists to prove one property of the spec: a server replica needs no memory of
previous requests to handle a multi-step MCP exchange correctly, because any
state that must survive between calls travels inside an opaque handle the
client passes back, not inside the process that handled the previous call.

Wire contract it implements (Streamable HTTP, JSON-RPC 2.0, one POST per call):
  - No `Mcp-Session-Id` header is required or honored. There is no
    `initialize` -> `initialized` handshake gate before a modern client can
    call a tool; `tools/list` and `tools/call` work on the very first request
    this process ever sees for a given client.
  - Every response carries `servedBy.pod` and `servedBy.podIP`, read from
    `HOSTNAME` and the container's own network interface, so the demo can
    show which replica actually answered each call.
  - Any state a tool needs across calls (here, a shopping cart) is returned
    to the caller as `cartToken`, a base64url JSON blob. The server does not
    keep a cart table in memory or in a shared store. Decoding the token is
    the only way the next call's state is recovered, which is why any
    replica, with zero shared state and zero session affinity, can service
    the next call in the sequence.

Supported JSON-RPC methods: initialize, notifications/initialized (ignored),
tools/list, tools/call. Tools: cart_create, cart_add_item, cart_checkout.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL_VERSION = "2026-07-28"
POD_NAME = os.environ.get("HOSTNAME", "unknown-pod")


def pod_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "0.0.0.0"


POD_IP = pod_ip()


def served_by() -> dict:
    return {"pod": POD_NAME, "podIP": POD_IP}


def encode_cart(state: dict) -> str:
    raw = json.dumps(state, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cart(token: str) -> dict:
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    return json.loads(raw)


TOOLS = [
    {
        "name": "cart_create",
        "description": "Start a new cart. Returns an opaque cartToken carrying all cart state; no server-side session is created.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cart_add_item",
        "description": "Add an item to a cart identified by cartToken. Returns an updated cartToken; the request may land on any replica.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cartToken": {"type": "string"},
                "item": {"type": "string"},
                "priceCents": {"type": "integer"},
            },
            "required": ["cartToken", "item", "priceCents"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cart_checkout",
        "description": "Checkout a cart identified by cartToken. Returns the final total; the request may land on any replica.",
        "inputSchema": {
            "type": "object",
            "properties": {"cartToken": {"type": "string"}},
            "required": ["cartToken"],
            "additionalProperties": False,
        },
    },
]


def call_tool(name: str, args: dict) -> dict:
    if name == "cart_create":
        state = {"items": [], "totalCents": 0, "createdAtPod": POD_NAME}
        token = encode_cart(state)
        return {"cartToken": token, "items": [], "totalCents": 0, "servedBy": served_by()}

    if name == "cart_add_item":
        state = decode_cart(args["cartToken"])
        state["items"].append({"item": args["item"], "priceCents": args["priceCents"]})
        state["totalCents"] += int(args["priceCents"])
        token = encode_cart(state)
        return {
            "cartToken": token,
            "items": state["items"],
            "totalCents": state["totalCents"],
            "servedBy": served_by(),
        }

    if name == "cart_checkout":
        state = decode_cart(args["cartToken"])
        return {
            "status": "confirmed",
            "items": state["items"],
            "totalCents": state["totalCents"],
            "cartCreatedAtPod": state.get("createdAtPod"),
            "servedBy": served_by(),
        }

    raise KeyError(name)


class Handler(BaseHTTPRequestHandler):
    server_version = "stateless-mcp-demo/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        sys.stderr.write(
            "%s - - [%s] pod=%s %s\n" % (self.address_string(), self.log_date_time_string(), POD_NAME, fmt % args)
        )

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("MCP-Protocol-Version", PROTOCOL_VERSION)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/healthz"):
            self._send_json(200, {"status": "ok", "servedBy": served_by(), "time": time.time()})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        # Notifications (no "id") get no body per JSON-RPC; ack with 202.
        is_notification = "id" not in req

        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stateless-mcp-demo", "version": "1.0.0"},
                "instructions": "Stateless demo server for the 2026-07-28 MCP spec. No Mcp-Session-Id is required.",
                "_meta": {"servedBy": served_by()},
            }
            self._send_json(200, {"jsonrpc": "2.0", "id": req_id, "result": result})
            return

        if method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if method == "tools/list":
            self._send_json(200, {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
            return

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                result = call_tool(name, args)
                self._send_json(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(result)}], "structuredContent": result},
                    },
                )
            except KeyError:
                self._send_json(
                    200,
                    {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown tool {name!r}"}},
                )
            except Exception as exc:  # noqa: BLE001 - demo server, surface the error to the caller
                self._send_json(
                    200,
                    {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(exc)}},
                )
            return

        if is_notification:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self._send_json(200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method {method!r}"}})


def main():
    port = int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"stateless-mcp-demo listening on :{port} pod={POD_NAME} ip={POD_IP}", file=sys.stderr)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
