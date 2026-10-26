# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.6.x | Yes |
| < 0.6 | No |

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on the repository
(`Security` → `Report a vulnerability`) rather than a public issue.

Include:

- affected version or commit;
- a minimal reproduction;
- the impact and any relevant logs;
- whether the issue requires a real device or network.

Do not include credentials, tokens, or personal network captures.

## Security model

Southbound is a local-first tool, not a hardened remote control service.

- The dashboard and MCP server bind to `127.0.0.1` by default.
- Discovery and scanning are intended for networks you own or are authorized
  to operate.
- Device access goes through `AccessHub`, which applies policy and audit.
- Commissioning a new device requires explicit owner confirmation; the
  runtime never scans for default credentials or self-propagates.
- Commission records in `data/commissioned.json` contain connection metadata
  only, never tokens or passwords.
- High-risk capabilities (locks, mains power, gas) are denied by default and
  require explicit policy enablement plus confirmation.
- Configuration tokens must come from environment variables, never from
  committed files.
- `data/` contains local inventories and audit logs; it is gitignored but may
  still contain sensitive network metadata. Protect it accordingly.

## Out of scope

- Unauthorized scanning or access to third-party networks/devices.
- Authentication bypass, exploit development, or self-propagation.
- Safety certification for physical actuators. Users are responsible for
  electrical and physical safety when connecting real devices.
