# Runtime Service

Southbound can run as one shared local runtime instead of embedding a separate
`SouthboundRuntime` inside every CLI, MCP, and dashboard process. This gives
all clients the same device graph, policy state, audit trail, commissioning
store, and event bus.

## Start the service

```bash
southbound serve --host 127.0.0.1 --port 8765
```

For a trusted-LAN bind, a bearer token is mandatory:

```bash
SOUTHBOUND_RUNTIME_TOKEN='long-random-token' \
  southbound serve --host 0.0.0.0 --port 8765
```

Loopback clients are exempt from the token so the local dashboard keeps
working. Non-loopback `/api/*` requests without a matching
`Authorization: Bearer …` header receive HTTP 401. A non-loopback bind with no
token is refused at startup.

`serve` owns:

- the device graph and adapters;
- `AccessHub` policy and audit;
- `CommissionStore` persistence;
- `EventBus` / SSE;
- the 2.5D scene runtime.

Do not start multiple `serve` processes against the same commissioned store
unless you have deliberately partitioned the devices.

## Connect MCP to it

Environment style:

```bash
SOUTHBOUND_REMOTE_URL=http://127.0.0.1:8765 \
SOUTHBOUND_REMOTE_TOKEN='long-random-token' \
  python3 -m southbound.mcp_server
```

Explicit CLI style:

```bash
southbound mcp --remote http://127.0.0.1:8765 --token 'long-random-token'
```

An MCP client configuration can use either. Example:

```json
{
  "mcpServers": {
    "southbound": {
      "type": "stdio",
      "command": "southbound",
      "args": ["mcp", "--remote", "http://127.0.0.1:8765"],
      "env": {
        "SOUTHBOUND_REMOTE_TOKEN": "long-random-token"
      }
    }
  }
}
```

When remote mode is active, MCP tools use the HTTP runtime surface:

- `GET /api/northbound/status`
- `GET /api/northbound/devices`
- `GET /api/northbound/devices/<device_id>`
- `GET /api/northbound/catalog`
- `GET /api/graph`
- `GET /api/graph/devices/<device_id>`
- `GET /api/state`
- `GET /api/interaction/endpoints`
- `GET /api/interaction/sessions`
- `POST /api/interaction/input`
- `POST /api/interaction/handoff`
- `POST /api/northbound/intent`
- `POST /api/northbound/call`
- `POST /api/northbound/commission`
- `POST /api/northbound/decommission`
- `GET /api/events/recent`
- simulator endpoints for `sim_devices` / `sim_action`

The MCP handler does not create a second runtime and does not write devices
locally when in remote mode.

## Connect CLI to it

```bash
SOUTHBOUND_REMOTE_URL=http://127.0.0.1:8765 \
SOUTHBOUND_REMOTE_TOKEN='long-random-token' \
  southbound access
SOUTHBOUND_REMOTE_URL=http://127.0.0.1:8765 \
SOUTHBOUND_REMOTE_TOKEN='long-random-token' \
  southbound call living_light turn_on
```

CLI commands that use the runtime (intent, call, tools, access, graph, interact,
handoff, onboard, decommission) honor `SOUTHBOUND_REMOTE_URL`. Discovery,
survey, and deep scan remain local commands because they do not own device
state.

## Design boundaries

- The service is the **only** action authority. The remote client cannot
  enforce policy; the service's `AccessHub` does.
- MCP is still the tool façade; it is not the runtime itself.
- CLI is a client in remote mode; it must not write a separate
  `data/commissioned.json` while the service is running.
- The dashboard already uses the service's in-process runtime.
- `RemoteRuntime` currently uses request/response calls and event replay; it
  does not maintain an SSE subscription. Use `/api/events` for the live stream.
- Browser `EventSource` cannot attach an `Authorization` header; a non-loopback
  dashboard should go through a local reverse proxy or remain on loopback.
- The service is process-local, not a multi-host cluster. For Pi/N100
  deployments, keep it bound to loopback or a trusted LAN with a future
  authentication/TLS layer.

## Security

The HTTP runtime service now supports a shared-secret bearer token:

- loopback clients are exempt so the local dashboard keeps working;
- non-loopback clients must send `Authorization: Bearer <token>`;
- a non-loopback bind without `SOUTHBOUND_RUNTIME_TOKEN` is refused;
- tokens should come from the environment, not shell history or committed
  config.

There is still **no TLS and no multi-user identity system**. Bind to
`127.0.0.1` unless a trusted LAN and a future TLS layer are in place. A remote
Pi harness should run on the same host or a trusted local network, and
high-risk capabilities still require policy + `confirm=true`; remote mode must
not be used to bypass those checks.
