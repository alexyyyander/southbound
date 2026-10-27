# Runtime Service

Southbound can run as one shared local runtime instead of embedding a separate
`SouthboundRuntime` inside every CLI, MCP, and dashboard process. This gives
all clients the same device graph, policy state, audit trail, commissioning
store, and event bus.

## Start the service

```bash
southbound serve --host 127.0.0.1 --port 8765
```

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
  python3 -m southbound.mcp_server
```

Explicit CLI style:

```bash
southbound mcp --remote http://127.0.0.1:8765
```

An MCP client configuration can use either. Example:

```json
{
  "mcpServers": {
    "southbound": {
      "type": "stdio",
      "command": "southbound",
      "args": ["mcp", "--remote", "http://127.0.0.1:8765"]
    }
  }
}
```

When remote mode is active, MCP tools use the HTTP runtime surface:

- `GET /api/northbound/status`
- `GET /api/northbound/devices`
- `GET /api/northbound/devices/<device_id>`
- `GET /api/northbound/catalog`
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
SOUTHBOUND_REMOTE_URL=http://127.0.0.1:8765 southbound access
SOUTHBOUND_REMOTE_URL=http://127.0.0.1:8765 southbound call living_light turn_on
```

CLI commands that use the runtime (intent, call, tools, access, onboard,
decommission) honor `SOUTHBOUND_REMOTE_URL`. Discovery, survey, and deep scan
remain local commands because they do not own device state.

## Design boundaries

- The service is the **only** action authority. The remote client cannot
  enforce policy; the service's `AccessHub` does.
- MCP is still the tool façade; it is not the runtime itself.
- CLI is a client in remote mode; it must not write a separate
  `data/commissioned.json` while the service is running.
- The dashboard already uses the service's in-process runtime.
- `RemoteRuntime` currently uses request/response calls and event replay; it
  does not maintain an SSE subscription. Use `/api/events` for the live stream.
- The service is process-local, not a multi-host cluster. For Pi/N100
  deployments, keep it bound to loopback or a trusted LAN with a future
  authentication/TLS layer.

## Security

There is currently **no authentication or TLS** on the HTTP runtime service.
Bind to `127.0.0.1` and do not expose it to a WAN or an untrusted LAN. A
remote Pi harness should run on the same host or a trusted local network until
token auth, TLS, and explicit confirmation transport are added.

High-risk capabilities still require policy and `confirm=true`; remote mode
must not be used to bypass those checks. The service owns the audit log, so
all remote calls are recorded in one place.
