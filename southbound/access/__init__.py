"""Access layer for IoT agents.

Public surface:

    AccessHub, DeviceBinding, Capability, Risk
    PolicyEngine, PolicyConfig, AuditLog
    build_hub, load_config
"""

from .audit import AuditLog
from .commissioning import CommissionStore
from .capabilities import (
    CAPABILITIES,
    build_catalog,
    capabilities_for,
    validate_arguments,
)
from .config import build_hub, load_config
from .errors import (
    AccessError,
    AdapterUnavailable,
    PolicyDenied,
    UnknownCapability,
    UnknownDevice,
    ValidationError,
)
from .hub import AccessHub
from .models import Capability, DeviceBinding, Risk
from .policy import Decision, PolicyConfig, PolicyEngine

__all__ = [
    "AccessHub",
    "DeviceBinding",
    "Capability",
    "Risk",
    "PolicyEngine",
    "PolicyConfig",
    "Decision",
    "AuditLog",
    "CommissionStore",
    "build_hub",
    "load_config",
    "CAPABILITIES",
    "capabilities_for",
    "build_catalog",
    "validate_arguments",
    "AccessError",
    "AdapterUnavailable",
    "PolicyDenied",
    "UnknownCapability",
    "UnknownDevice",
    "ValidationError",
]
