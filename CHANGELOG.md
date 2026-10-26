# Changelog

All notable changes to Southbound are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
