"""Small, dependency-free auth helpers for the local runtime service.

The HTTP service is intended for loopback use. When it is bound to a LAN
address, callers outside loopback must present a bearer token. This is a
minimal shared-secret boundary, not a multi-user identity system.
"""

from __future__ import annotations

import hmac

LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}


def bearer_token(header: str | None) -> str:
    value = (header or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()


def is_authorized(
    client_address: str,
    authorization_header: str | None,
    expected_token: str,
) -> bool:
    """Allow loopback without a token; require a matching bearer elsewhere."""
    if not expected_token:
        return True
    if client_address in LOOPBACK_CLIENTS:
        return True
    supplied = bearer_token(authorization_header)
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected_token)
