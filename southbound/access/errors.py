"""Access-layer errors."""

from __future__ import annotations


class AccessError(Exception):
    """Base class for access-layer failures."""


class UnknownDevice(AccessError):
    pass


class UnknownCapability(AccessError):
    pass


class ValidationError(AccessError):
    pass


class PolicyDenied(AccessError):
    def __init__(self, reason: str, requires_confirmation: bool = False) -> None:
        super().__init__(reason)
        self.requires_confirmation = requires_confirmation


class AdapterUnavailable(AccessError):
    pass
