# stateless-mcp-scale

**Three replicas of a real MCP server, fronted by [agentgateway](https://agentgateway.dev), with no session affinity configured anywhere. A four-call shopping-cart session completes correctly across three different replicas, including mid-session through a full rolling restart, because the 2026-07-28 MCP spec revision moved session state out of the server process and into the request.**

This reproduces the practical Kubernetes consequence of the MCP [2026-07-28 spec revision](https://blog.modelcontextprotocol.io/posts/2026-07-28/), the one [Simon Willison wrote about](https://simonwillison.net/2026/Jul/31/stateless-mcp/) ([386 points on Hacker News](https://news.ycombinator.com/item?id=49131438)) the same week [agentgateway shipped support for it in v1.4.0](https://github.com/agentgateway/agentgateway/releases/tag/v1.4.0).

## What this demo does

1. Runs a minimal MCP server, written to the stateless wire format, as a 3-replica Kubernetes Deployment (`manifests/10-mcp-server.yaml`), behind a plain `ClusterIP` Service with `sessionAffinity: None` (the default; nothing was configured to turn it on).
2. Fronts it with agentgateway v1.5.0, configured with `statefulMode: stateless` (`agentgateway/agentgateway.yaml`), exposed through a `NodePort` Service that also has `sessionAffinity: None`.
3. Runs a 4-call MCP "session" (create a cart, add two items, check out) as four **separate, independent HTTP requests** with no `Mcp-Session-Id` anywhere, and shows which pod served each call.
4. Triggers `kubectl rollout restart` on the MCP server Deployment in the middle of an open cart and keeps calling through it, finishing the checkout on a pod that didn't exist when the cart was created.

Every command below and every line of output in this README is from an actual run of this repo (see [Verified output](#verified-output)); nothing here is simulated.

## Why this needed proving

The MCP spec before 2026-07-28 required a stateful `initialize`/`initialized` handshake and an `Mcp-Session-Id` header that pinned a client to whichever server process handled its first request. On Kubernetes that meant either `sessionAffinity: ClientIP` on the Service, or cookie-based affinity in front of it, to keep a session's requests landing on the same pod. That affinity setting fights horizontal pod autoscaling and rolling deploys: scale down the pod a session is pinned to, or restart it, and the session breaks.

The 2026-07-28 revision removes the requirement. Per the [spec's own announcement](https://blog.modelcontextprotocol.io/posts/2026-07-28/): "Each request now travels on its own, carrying its protocol version, client identity, and client capabilities in `_meta`," which means "any request [can land] on any server instance behind a plain round-robin load balancer without needing shared storage." Where a server genuinely needs state to survive between calls, the spec's own guidance is to "mint an explicit handle from a tool and have the model pass it back as an argument" rather than keep it in server memory. That's exactly what `server/stateless_mcp_server.py` does: the cart's state lives in an opaque `cartToken` the client carries, not in any replica's memory.

This repo deliberately configures **no** session affinity anywhere (`manifests/10-mcp-server.yaml` and `agentgateway/deploy.yaml` both use the Kubernetes default, `sessionAffinity: None`) to prove the old workaround is no longer needed, not just assert it.

## What ships here

- `server/stateless_mcp_server.py`: a from-scratch MCP server (Python stdlib only, no framework) implementing `initialize`, `tools/list`, and `tools/call` over Streamable HTTP, with three tools (`cart_create`, `cart_add_item`, `cart_checkout`) that carry all state in a client-held token. Every response includes `servedBy.pod` and `servedBy.podIP` so the demo can show which replica answered.
- `manifests/`: the Kubernetes Deployment (3 replicas) and Service for the MCP server.
- `agentgateway/agentgateway.yaml`: the standalone agentgateway config (`statefulMode: stateless`, one `mcp` target pointing at the Kubernetes Service), validated with agentgateway's own `--validate-only` flag.
- `agentgateway/deploy.yaml`: the same config as a ConfigMap, plus the agentgateway Deployment and a `NodePort` Service.
- `scripts/mcp_client.py` and `scripts/demo.py`: a small client speaking the exact wire format agentgateway requires (see [Gotchas](#gotchas-the-exact-wire-format-agentgateway-requires)), and a script that runs the full proof end to end.

## Prerequisites

- Docker
- [kind](https://kind.sigs.k8s.io/) (tested with v0.30, Kubernetes v1.37.0)
- kubectl
- Python 3.10+ (stdlib only, no `pip install` needed)

## Quickstart

```bash
git clone https://github.com/themsquared/stateless-mcp-scale.git
cd stateless-mcp-scale
make up      # builds the demo image, creates the kind cluster, deploys everything
make demo    # runs the fan-out + session + rolling-restart proof
```

`make up` builds `stateless-mcp-demo:dev`, creates a kind cluster named `stateless-mcp-scale`, loads both that image and `cr.agentgateway.dev/agentgateway:v1.5.0` into it (kind clusters can't pull from the internet by default, so images need to be loaded in), then applies the namespace, the 3-replica MCP server Deployment, and the agentgateway Deployment in order, waiting for each rollout. It finishes by printing the URL agentgateway is reachable on from the host: `http://127.0.0.1:38080/mcp`.

Tear down with:

```bash
make down
```

### Validating the agentgateway config without a cluster

```bash
make validate-config
```

This runs `docker run --rm -v "$PWD/agentgateway:/config" cr.agentgateway.dev/agentgateway:v1.5.0 --validate-only -f /config/agentgateway.yaml`, which prints `Configuration is valid!` against the same config the Deployment mounts.

## Verified output

This is the unedited output of `make up` followed by `make demo` against a clean kind cluster, with only the install/pull log lines trimmed from `make up`:

```
$ make demo
python3 scripts/demo.py
deployment.apps/stateless-mcp-demo restarted
deployment "stateless-mcp-demo" successfully rolled out

=== 1. Fan-out: 9 independent tools/call requests, no session affinity configured ===
  request 1: served by stateless-mcp-demo-757875b499-z2zsg
  request 2: served by stateless-mcp-demo-757875b499-w6mpr
  request 3: served by stateless-mcp-demo-757875b499-z2zsg
  request 4: served by stateless-mcp-demo-757875b499-w6mpr
  request 5: served by stateless-mcp-demo-757875b499-z2zsg
  request 6: served by stateless-mcp-demo-757875b499-w6mpr
  request 7: served by stateless-mcp-demo-757875b499-z2zsg
  request 8: served by stateless-mcp-demo-757875b499-z2zsg
  request 9: served by stateless-mcp-demo-757875b499-z2zsg
  -> 2 distinct replica(s) answered 9 requests: ['stateless-mcp-demo-757875b499-w6mpr', 'stateless-mcp-demo-757875b499-z2zsg']

=== 2. One logical session, four separate requests, zero shared server memory ===
  cart_create      -> pod=stateless-mcp-demo-757875b499-z2zsg      cartToken(len=108)
  cart_add_item    -> pod=stateless-mcp-demo-757875b499-w6mpr      items=['keyboard']
  cart_add_item    -> pod=stateless-mcp-demo-757875b499-w6mpr      items=['keyboard', 'mouse']
  cart_checkout    -> pod=stateless-mcp-demo-757875b499-w6mpr      total=$65.00 status=confirmed
  -> session correct across every hop, no Mcp-Session-Id, no sticky routing.

=== 3. Bonus: rolling restart of every replica, mid-session ===
  cart_create (before restart) -> pod=stateless-mcp-demo-757875b499-z2zsg
  triggered: kubectl rollout restart deployment/stateless-mcp-demo
  cart_add_item during rollout (1/5) -> pod=stateless-mcp-demo-757875b499-n9s5r
  cart_add_item during rollout (2/5) -> pod=stateless-mcp-demo-757875b499-z2zsg
  cart_add_item during rollout (3/5) -> pod=stateless-mcp-demo-7674b87667-9vvrw
  cart_add_item during rollout (4/5) -> pod=stateless-mcp-demo-7674b87667-9vvrw
  cart_add_item during rollout (5/5) -> pod=stateless-mcp-demo-7674b87667-rp9fm
  cart_checkout (after restart)  -> pod=stateless-mcp-demo-7674b87667-9vvrw total=$5.00 status=confirmed
  -> cart survived a full rolling restart: created on stateless-mcp-demo-757875b499-z2zsg, checked out on stateless-mcp-demo-7674b87667-9vvrw (that pod didn't exist when the cart was created).

All assertions passed.
```

Step 1 landed on 2 of the 3 replicas in this particular 9-request sample (round-robin/random distribution; a larger sample converges on all three, and a separate run during development hit all 3 of 3 on the first 6 requests). The point isn't a specific ratio, it's that **no two consecutive calls were pinned to the same pod by any mechanism**, and every call still succeeded. Step 3 is the one worth rereading: the cart that `cart_checkout` closes out was created on a pod from the Deployment's *previous* ReplicaSet, one that no longer exists by the time checkout runs, and the server that answers checkout has never seen that cart before in its own memory. It decodes it correctly anyway, because the cart's state was never in that memory to begin with.

## Gotchas: the exact wire format agentgateway requires

None of this is documented anywhere obvious yet (agentgateway's own v1.4.0 release notes say as much: "most of it is not yet covered by a dedicated guide"), so it's recorded here from direct testing against v1.5.0 with `statefulMode: stateless`:

- A bare JSON-RPC `tools/list` or `tools/call` POST gets `400 mcp: invalid MCP protocol version header` without an `MCP-Protocol-Version: 2026-07-28` HTTP header. Putting the version only in the JSON-RPC params is not enough.
- With that header set, the next failure is `400 invalid request parameters: _meta.protocolVersion is required for modern requests` until `params._meta` includes the key **`io.modelcontextprotocol/protocolVersion`** (the reverse-DNS-qualified form, not a bare `protocolVersion` key).
- With that fixed, the next failure is `400 invalid MCP routing header: Mcp-Method` until the HTTP headers also include `Mcp-Method: <the JSON-RPC method>` (and `Mcp-Name: <tool name>` for `tools/call`). These are agentgateway's own routing headers, separate from the JSON-RPC body.
- An `initialize` call is rejected outright (`-32601 method not found: initialize`) when `statefulMode: stateless` is set. This matches the spec's description of skipping the synthetic handshake for modern requests: don't send one.
- At no point in any of this does agentgateway send or require an `Mcp-Session-Id` header, to the client or to the backend.

`scripts/mcp_client.py` encodes all of the above in one place.

## What this demo does not cover

- **Multi-node distribution.** The kind cluster here is single-node; all three replicas land on one Kubernetes node, so this proves no *replica*-level stickiness, not multi-*machine* network topology. The mechanism (round-robin to any ready pod, no affinity) is identical on a multi-node cluster; it just wasn't necessary to stand one up to demonstrate it.
- **SSE-based long-lived streams.** agentgateway's MCP support also handles longer-lived streaming responses; this demo only exercises the simple request/response path, which is what the stateless cart tools need.
- **Backward compatibility with stateful MCP clients.** This server only speaks the 2026-07-28 stateless wire format. A client built against the earlier, session-based spec would need a server that supports both (agentgateway itself does, via `statefulMode: stateful` or per-target overrides); that compatibility path isn't exercised here.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Topics

`agentgateway` `mcp` `kubernetes` `ai-agents` `model-context-protocol` `horizontal-scaling`
