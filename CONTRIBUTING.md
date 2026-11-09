# Contributing to Southbound

Thanks for taking a look. Southbound is a local-first toolchain for
discovering LAN devices and exposing them to agents through a policy-checked
access layer. Contributions are welcome in code, adapters, docs, and tests.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the test suite:

```bash
make test
# or
python -m unittest discover -s tests -v
```

Run the linter:

```bash
make lint
# or
ruff check .
```

Start the dashboard:

```bash
python -m southbound demo
```

The MQTT adapter is optional:

```bash
python -m pip install -e ".[mqtt]"
```

## Project layout

- `southbound/` — Python package and web assets.
- `southbound/access/` — device/capability model, policy, audit, adapters.
- `southbound/analysis/` — analysis contracts, backend port, and REA MCP adapter; independent of devices.
- `config/` — example access-layer and analysis-backend configuration.
- `docs/` — architecture, access layer, comparison, chips, signals, hardware.
- `tests/` — standard-library unittest suite.
- `data/` — generated inventories, scans, and audit logs (gitignored).

## Adding an adapter

1. Subclass `DeviceAdapter` in `southbound/access/adapters/`.
2. Implement `bindings()` using `capabilities_for(kind, allowed)`.
3. Implement `call()` for protocol execution only. Do not duplicate policy
   or audit logic; `AccessHub` owns those.
4. Register the adapter in `southbound/access/config.py`.
5. Add an example to `config/access.example.json` and a test.
6. Document the protocol, dependencies, and limitations in
   `docs/ACCESS_LAYER.md`.

## Tests

- Prefer deterministic tests; use the simulator instead of real devices.
- Add tests for success, validation failure, policy denial, and unavailable
  adapters.
- Do not make tests depend on a live LAN, MQTT broker, or Home Assistant
  instance.

## Analysis backend changes

Implement `AnalysisBackend` independently of `DeviceAdapter`/`AccessHub`. Keep MCP
inputs, outputs, errors and provenance aligned with `analysis/contracts.py`.
Use test doubles only in tests; an unconfigured backend must remain unavailable.

Run interface and subprocess tests without installing an engine:

```bash
python -m unittest discover -s tests -p 'test_analysis_mcp.py' -v
python -m unittest discover -s tests -p 'test_rea_backend.py' -v
```

Real-engine validation is explicit and requires a configured backend and `cc`:

```bash
SOUTHBOUND_REA_LIVE_CONFIG=/absolute/path/analysis.local.json \
python -m unittest discover -s tests -p 'test_rea_live.py' -v
```

Do not commit local configs, input binaries, evidence or runtime dependencies.
Update the [contract](docs/DECOMPILATION_MCP.md), [deployment guide](docs/REA_BACKEND.md),
and changelog when the analysis surface changes. On external disks, exclude macOS
AppleDouble metadata from linting: `ruff check --extend-exclude '._*' .`.

## Code style

- Python 3.10+; standard library first.
- Keep the public surface small and explicit.
- Type hints on new public functions.
- No secrets in code or config examples; use environment variables.
- Run `ruff check .` and the test suite before opening a PR.

## Safety and ethics

- Only scan, sniff, or control networks and devices you own or are authorized
  to operate.
- Never add authentication bypass, exploit, or self-propagation features.
- High-risk capabilities (locks, mains power, gas) must remain behind policy
  and explicit confirmation.
- Do not let an LLM bypass `AccessHub` for high-risk actions.

## Pull requests

- Keep changes focused and explain the user-visible behavior.
- Include tests and documentation updates.
- Note any new dependency and why it is optional or required.
- Describe how you verified the change (commands, hardware, screenshots).
