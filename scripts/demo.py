#!/usr/bin/env python3
"""Run the full proof this repo makes, against a live cluster, and print it.

1. Fan-out: several independent tools/call requests, no session, and show
   they land on different `stateless-mcp-demo` replicas.
2. Multi-step session: cart_create -> cart_add_item -> cart_add_item ->
   cart_checkout, as four SEPARATE requests, logging which pod served each
   one, proving the exchange completes correctly with no session affinity
   anywhere in front of it.
3. Resilience: start a cart, trigger `kubectl rollout restart` on the MCP
   server Deployment, keep calling throughout, and finish the checkout after
   every original pod has been replaced.

Usage: python3 scripts/demo.py [base_url]
"""

from __future__ import annotations

import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from mcp_client import tool_result  # noqa: E402

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:38080/mcp"
CTX = "kind-stateless-mcp-scale"
NS = "stateless-mcp-scale"


def line(title: str) -> None:
    print(f"\n=== {title} ===")


def pod_of(result: dict) -> str:
    return result["servedBy"]["pod"]


def main() -> None:
    line("1. Fan-out: 9 independent tools/call requests, no session affinity configured")
    pods_hit = set()
    for i in range(9):
        result = tool_result(BASE_URL, "cart_create", {}, req_id=100 + i)
        pod = pod_of(result)
        pods_hit.add(pod)
        print(f"  request {i + 1}: served by {pod}")
    print(f"  -> {len(pods_hit)} distinct replica(s) answered 9 requests: {sorted(pods_hit)}")

    line("2. One logical session, four separate requests, zero shared server memory")
    r = tool_result(BASE_URL, "cart_create", {}, req_id=200)
    print(f"  cart_create      -> pod={pod_of(r):40s} cartToken(len={len(r['cartToken'])})")
    token = r["cartToken"]

    r = tool_result(BASE_URL, "cart_add_item", {"cartToken": token, "item": "keyboard", "priceCents": 4500}, req_id=201)
    print(f"  cart_add_item    -> pod={pod_of(r):40s} items={[i['item'] for i in r['items']]}")
    token = r["cartToken"]

    r = tool_result(BASE_URL, "cart_add_item", {"cartToken": token, "item": "mouse", "priceCents": 2000}, req_id=202)
    print(f"  cart_add_item    -> pod={pod_of(r):40s} items={[i['item'] for i in r['items']]}")
    token = r["cartToken"]

    r = tool_result(BASE_URL, "cart_checkout", {"cartToken": token}, req_id=203)
    print(f"  cart_checkout    -> pod={pod_of(r):40s} total=${r['totalCents'] / 100:.2f} status={r['status']}")
    assert r["totalCents"] == 6500 and r["status"] == "confirmed"
    print("  -> session correct across every hop, no Mcp-Session-Id, no sticky routing.")

    line("3. Bonus: rolling restart of every replica, mid-session")
    r = tool_result(BASE_URL, "cart_create", {}, req_id=300)
    created_pod = pod_of(r)
    token = r["cartToken"]
    print(f"  cart_create (before restart) -> pod={created_pod}")

    subprocess.run(
        ["kubectl", "--context", CTX, "-n", NS, "rollout", "restart", "deployment/stateless-mcp-demo"],
        check=True,
    )
    print("  triggered: kubectl rollout restart deployment/stateless-mcp-demo")

    for i in range(5):
        r = tool_result(BASE_URL, "cart_add_item", {"cartToken": token, "item": f"widget-{i + 1}", "priceCents": 100}, req_id=301 + i)
        token = r["cartToken"]
        print(f"  cart_add_item during rollout ({i + 1}/5) -> pod={pod_of(r)}")
        time.sleep(1)

    subprocess.run(
        ["kubectl", "--context", CTX, "-n", NS, "rollout", "status", "deployment/stateless-mcp-demo", "--timeout=60s"],
        check=True,
    )

    r = tool_result(BASE_URL, "cart_checkout", {"cartToken": token}, req_id=310)
    print(f"  cart_checkout (after restart)  -> pod={pod_of(r)} total=${r['totalCents'] / 100:.2f} status={r['status']}")
    assert r["totalCents"] == 500 and r["status"] == "confirmed"
    if r["servedBy"]["pod"] != created_pod:
        print(f"  -> cart survived a full rolling restart: created on {created_pod}, checked out on {r['servedBy']['pod']} (that pod didn't exist when the cart was created).")

    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()
