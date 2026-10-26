"""Compatibility entry point for `python3 iotlab.py`."""

from iot_agent_lab.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
