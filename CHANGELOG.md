# Changelog

All notable changes to Southbound are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Durable LAN graph**: SQLite-backed device identities, control scope,
  authorization, provenance, capabilities, cached state, and last-seen times.
  Inventory, deep scan, commissioning records, and adapter bindings merge into
  one logical device; authorized bindings keep their canonical `device_id`.
- `GET /api/graph`, `GET /api/graph/devices/<device_id>`, and `GET /api/state`
  plus `RemoteRuntime.graph()` / `graph_device()`.
- `southbound graph [--json] [--device ID]` CLI and the `lan_graph` MCP tool.
- **Durable event log**: event ids continue across restarts and
  `/api/events/recent?since=` can replay persisted events.
- **Home Assistant WebSocket events**: optional `websocket-client` support for
  `state_changed` events, with auth/subscribe handshake, reconnect backoff,
  ping/pong, adapter event status, and the same EventBus/graph path as MQTT
  and BLE/NFC events.
- **Multi-endpoint interaction plane**: endpoint profiles, sessions, modality
  routing, cross-endpoint outputs, handoff, `/interaction` dashboard, HTTP API,
  `southbound interact`, and MCP `interaction_endpoints` / `interaction_input`.
- Tests for graph identity merging, persistence, runtime state projection,
  event replay, HA WebSocket handshake/state mapping, and interaction routing
  (122 tests total).

## [0.11.0] - 2026-10-27

### Added

- Optional Bearer-token authentication for the shared runtime service.
  Loopback clients remain exempt; non-loopback API clients need
  `SOUTHBOUND_REMOTE_TOKEN`; non-loopback binds without a token are refused.
- `southbound serve --token`, `southbound mcp --token`, and the corresponding
  environment variables.
- Tests for the auth helper and token header propagation (89 tests total).

### Changed

- Remote MCP/CLI docs now show token configuration; TLS and multi-user
  identity remain future work.

## [0.10.0] - 2026-10-27

### Added

- **Shared runtime service**: `southbound serve` is now a single runtime that
  Dashboard, MCP, and CLI clients can share; `RemoteRuntime` talks to it over
  the local HTTP API instead of embedding a second device graph.
- **Remote MCP/CLI mode**: `southbound mcp --remote URL` and
  `SOUTHBOUND_REMOTE_URL` connect to the shared runtime; device describe and
  include-address HTTP endpoints are now available.
- Shared adapter registry for configuration, CLI, MCP, and dashboard onboarding.
- Optional BLE GATT and NFC reader adapters, with explicit simulated drivers,
  bound connection fields, policy-gated writes, and lifecycle cleanup.
- Unified device-originated events for MQTT state, BLE notifications, and NFC
  tag presentations, delivered through the existing EventBus/SSE surface.
- Hardware configuration examples, an in-process no-hardware demo, and tests
  covering simulated flows and fake Bleak/nfcpy boundaries. Real hardware
  compatibility has not yet been verified.
- Tests for the remote runtime client and CLI remote mode (85 tests total).

### Changed

- MQTT read calls establish subscriptions, reconnect callbacks restore them,
  and decommission/close release subscriptions and connections.
- `SOUTHBOUND_CONFIG` selects an explicit configuration file for all entry points.
- Runtime docs distinguish embedded mode from the shared service; remote mode
  is loopback-only until authentication/TLS is added.

## [0.9.0] - 2026-10-27

### Added

- **Hostless `southbound survey`**: IP/mDNS/SSDP inventory, optional BLE
  advertisements, Wi-Fi band survey, radio/coordinator evidence, and
  service fingerprinting limited to discovered hosts.
- **Adapter readiness report**: MQTT, Home Assistant, camera, Zigbee, Thread,
  Matter, BLE, and drone status with a recommended harness profile.
- **Raspberry Pi / Linux support** for host discovery, ARP, mDNS (avahi),
  Wi-Fi (nmcli), Bluetooth and serial radio detection.
- **Pi harness profile**: `pi_minimal` and `pi_microhub`, plus a systemd unit
  example under `deploy/systemd/`.
- Tests for survey parsing, readiness, Pi profile selection, and the CLI
  (61 tests total).

### Changed

- Version bump to `0.9.0`; survey reports are written under `data/` and stay
  gitignored.

## [0.8.0] - 2026-10-26

### Added

- **Synthetic splat preview** in `/scene`: a dependency-free WebGL point-splat
  renderer generated from the procedural room geometry, with orbit/zoom,
  projected device hotspots, and a moving avatar marker.
- **2.5D / Splat view toggle**: both views share the same presence,
  automation, SSE events, device inspector, and AccessHub path.
- **Visual fallback**: WebGL-less browsers keep the 2.5D control map and the
  view toggle disables cleanly.
- Documentation for the synthetic preview and the future captured 3DGS asset
  pipeline.

### Changed

- Version bump to `0.8.0`.
- `docs/SCENE.md` distinguishes the shipped synthetic preview from a trained
  Gaussian Splatting capture.

## [0.7.0] - 2026-10-26

### Added

- **2.5D room experiment** at `/scene`: movable avatar, presence zones,
  clickable devices, automatic low-risk reflex rules, agent notices, SSE
  event feed, and read-back verification.
- **Scene API**: `/api/scene/state`, `/api/scene/presence`,
  `/api/scene/automation`, and `/api/scene/reset`.
- **SceneController**: deterministic presence/automation layer that still
  calls `SouthboundRuntime.call()` → `AccessHub`, never a bypass path.
- Tests: scene presence, automation, cooldown, clamping, reset, and mode
  coverage (55 tests total).
- Repository presentation: Southbound logo, GitHub social preview, brand
  guide (`assets/README.md`), and About / Demo / Protocol docs.
- README logo, badges, documentation navigation, naming note, and
  contribution section; GitHub description and topics.

### Changed

- Version bump to `0.7.0`; 3D Gaussian Splatting is documented as an
  optional visual layer over the authoritative 2.5D control map.

## [0.6.0] - 2026-10-26

### Added

- **Controlled onboarding/commissioning**: `SouthboundRuntime.commission()`
  and `decommission()` with explicit confirmation, policy scope checks,
  least-privilege capabilities, audit, and event flow.
- **Dynamic device registration**: `AccessHub.register_binding()` /
  `unregister_binding()`; simulator, Home Assistant, MQTT, and
  Zigbee2MQTT adapters implement `add_device` / `remove_device` / `verify_device`.
- **CommissionStore**: non-secret, gitignored persistence in
  `data/commissioned.json`; runtimes restore authorized devices on startup.
- **Northbound API**: `POST /api/northbound/commission` and
  `/decommission`.
- **CLI**: `southbound onboard` and `southbound decommission`.
- **MCP**: `iot_commission` and `iot_decommission` (15 tools total).
- **Dashboard**: “接入设备” onboarding dialog, commission/decommission
  events in the flowbar, and remove buttons for commissioned virtual devices.
- **Docs**: `docs/ONBOARDING.md` with the full lifecycle, API, policy,
  persistence, and safety boundaries.
- Tests: commissioning, policy, persistence, and lifecycle coverage
  (46 tests total).

### Changed

- Home Assistant and MQTT adapters now report availability based on
  transport configuration, not on an existing device list, so they can
  commission their first device.
- Policy configuration adds `allow_auto_commission` (default false).

## [0.5.0] - 2026-10-26

### Added

- **SouthboundRuntime** (`southbound/runtime.py`): one object owning the
  simulator, `AccessHub`, audit log, and a thread-safe `EventBus`.
- **Northbound API**: `POST /api/northbound/intent`, `/call`, and
  `GET /api/northbound/status|devices|catalog`; `/api/agent` remains an
  alias.
- **Live event stream**: `GET /api/events` (SSE) and
  `GET /api/events/recent?since=` for replay; events carry `plane`
  (`northbound`/`southbound`) and `direction`.
- **Dashboard flowbar**: animated Agent → Southbound → Devices flow with
  live intent/command/state labels and event count.
- **CLI**: `southbound intent` and `southbound serve`; `sim` remains an
  alias of intent. All CLI and MCP actions now route through the runtime.
- Tests: runtime/event-bus coverage (40 tests total).

### Changed

- MCP `sim_action` / `sim_goal` and `iot_call` now use the shared runtime,
  so every demo action is policy-checked, audited, and emitted as an event.

## [0.4.0] - 2026-10-26

### Added

- **Access layer** (`southbound/access/`): unified `DeviceBinding` /
  `Capability` / `Risk` model, JSON Schema argument validation, policy
  engine, JSONL audit log, capability catalog, and `AccessHub`.
- **Adapters**: simulator, Home Assistant REST, MQTT, and Zigbee2MQTT.
- **MCP tools**: `iot_hub_status`, `iot_devices_list`,
  `iot_device_describe`, `iot_tool_catalog`, `iot_call` (13 tools total).
- **CLI**: `southbound tools`, `southbound call`, `southbound access`.
- **Dashboard**: access-layer status in the rail; simulator and demo-agent
  actions now flow through `AccessHub` for policy and audit.
- Configuration example: `config/access.example.json`.
- Documentation: `docs/ACCESS_LAYER.md`, `docs/COMPARISON.md`, this
  changelog, license, contribution guide, security policy, and CI.
- Tests: access-layer policy/audit/catalog coverage (35 tests total).

### Changed

- Renamed the project, Python package, and CLI from `iot-agent-lab` /
  `iot_agent_lab` / `iot-lab` to **Southbound** / `southbound`.
- Upgraded the expected Python baseline to 3.10+ with a 3.10–3.13 CI matrix.
- Moved documentation into `docs/` and the dashboard assets into the
  package for wheel packaging.

## [0.3.0] - 2026-10-26

### Added

- LAN discovery with ARP, mDNS, and SSDP; `/23` subnet support.
- Targeted deep scan with HTTP/Telnet/SSH/SMB/RTSP/UPnP fingerprints.
- Vendor OUI lookup and T0–T3 agent capability classification.
- Deterministic inventory agent and integration planning.
- Six-device simulator and observation dashboard.
- stdio MCP server with discovery, deep scan, and simulator tools.
- Initial docs: architecture, chips, signals, hardware, and scan report.
