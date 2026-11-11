"""HTTP polling transport. Terminal credentials never authorize northbound APIs."""
from __future__ import annotations

import hmac
import json

from .access.errors import PolicyDenied
from .service_auth import bearer_token, is_authorized
from .terminals import TerminalConflict, TerminalUnauthorized


def runtime_authorized(client_address, authorization_header, expected_token):
    """Supplied credentials must be runtime credentials, including on loopback.

    Preserve anonymous local tools, but never upgrade a device token to owner.
    """
    if authorization_header:
        supplied = bearer_token(authorization_header)
        return bool(expected_token and supplied and hmac.compare_digest(supplied, expected_token))
    return is_authorized(client_address, authorization_header, expected_token)


class TerminalHTTPMixin:
    def _terminal_request(self, path: str, method: str) -> bool:
        if not (path.startswith("/api/terminal/") or path.startswith("/api/terminals/")):
            return False
        service = getattr(self.server, "terminal_service", None)
        if service is None:
            self._json({"ok": False, "error": "terminal service unavailable"}, status=503)
            return True
        token = bearer_token(self.headers.get("Authorization"))
        try:
            if path.startswith("/api/terminals/"):
                expected = getattr(self.server, "runtime_token", "")
                # Owner endpoints require a real credential even on loopback.
                if not expected or not token or not hmac.compare_digest(token, expected):
                    raise TerminalUnauthorized("owner runtime credential required")
            elif path not in ("/api/terminal/pair", "/api/terminal/setup-status"):
                service.adapter.store.authenticate(token)
            if method == "GET" and path.startswith("/api/terminals/images/"):
                metadata, data = service.image(path.rsplit("/", 1)[-1])
                self.send_response(200)
                self.send_header("Content-Type", metadata["mime_type"])
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
                return True
            if method != "POST":
                self._json({"ok": False, "error": "POST required"}, status=405)
                return True
            if path == "/api/terminal/images":
                from .terminal_images import MAX_IMAGE_BYTES
                length = int(self.headers.get("Content-Length", "0"))
                if (self.headers.get("Content-Type") != "image/jpeg" or not 0 < length <= MAX_IMAGE_BYTES
                        or self.headers.get("Transfer-Encoding")):
                    raise ValueError("bounded image/jpeg upload required")
                self.connection.settimeout(10)
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("incomplete upload")
                self._json(service.upload_image(token, self.headers.get("X-Command-Id", ""), data))
                return True
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                raise ValueError("application/json required")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                raise ValueError("body must contain 1..32768 bytes")
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("transfer encoding not supported")
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length), parse_constant=lambda _: None)
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            if path == "/api/terminals/pairings":
                result = service.create_pairing(body)
            elif path == "/api/terminals/capabilities":
                result = service.capabilities(body)
            elif path == "/api/terminal/hello":
                result = service.hello(token, body)
            elif path == "/api/terminal/pair":
                result = service.pair(body)
            elif path == "/api/terminal/poll":
                result = service.poll(token)
            elif path == "/api/terminal/receipt":
                result = service.receipt(token, body)
            elif path == "/api/terminal/progress":
                result = service.progress(token, body)
            elif path == "/api/terminal/notification-click":
                result = service.clicked(token, body)
            elif path == "/api/terminal/setup-status":
                result = service.setup_status(body)
            elif path in ("/api/terminal/self-test", "/api/terminal/self-test/status"):
                result = service.self_test(token, body, status_only=path.endswith("/status"))
            else:
                self._json({"ok": False, "error": "unknown terminal route"}, status=404)
                return True
            self._json(result)
        except TerminalUnauthorized as exc:
            self._json({"ok": False, "error": str(exc)}, status=401)
        except PolicyDenied as exc:
            self._json({"ok": False, "error": str(exc)}, status=403)
        except TerminalConflict as exc:
            self._json({"ok": False, "error": str(exc)}, status=409)
        except KeyError:
            self._json({"ok": False, "error": "unknown command"}, status=404)
        except (ValueError, TypeError, UnicodeError) as exc:
            self._json({"ok": False, "error": str(exc)}, status=400)
        return True
