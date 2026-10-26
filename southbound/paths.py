"""Filesystem locations for the toolchain.

Source checkout layout:

    <repo>/southbound/...   code + web assets
    <repo>/data/                generated inventories and audit log
    <repo>/config/              access-layer configuration
    <repo>/docs/                human documentation
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

DATA_DIR = Path(os.environ.get("IOT_LAB_DATA_DIR", PROJECT_ROOT / "data")).resolve()
CONFIG_DIR = Path(
    os.environ.get("IOT_LAB_CONFIG_DIR", PROJECT_ROOT / "config")
).resolve()
WEB_DIR = PACKAGE_DIR / "web"
DOCS_DIR = PROJECT_ROOT / "docs"
AUDIT_FILE = DATA_DIR / "audit.jsonl"


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR
