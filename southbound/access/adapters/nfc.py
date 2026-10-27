"""NFC reader access through nfcpy, with bounded reads and stoppable tag listeners."""

from __future__ import annotations

import threading
import time

from ..errors import AdapterUnavailable, ValidationError
from .hardware import HardwareAdapter


def _uid(value: str) -> str:
    try:
        raw = bytes.fromhex(value)
    except (ValueError, TypeError) as exc:
        raise ValidationError("expected_uid must be hexadecimal") from exc
    if not raw or len(raw) > 32:
        raise ValidationError("UID must contain 1–32 bytes")
    return raw.hex()


def _simulated_tag(uid: str, text: str) -> dict:
    # One NFC Forum well-known Text record, UTF-8, language "en".
    payload = b"\x02en" + text.encode("utf-8")
    if len(payload) < 256:
        header = bytes((0xD1, 1, len(payload)))
    else:
        header = bytes((0xC1, 1)) + len(payload).to_bytes(4, "big")
    octets = header + b"T" + payload
    return {
        "uid": uid,
        "ndef_hex": octets[:4096].hex(),
        "ndef_truncated": len(octets) > 4096,
        "writeable": True,
    }


class NfcAdapter(HardwareAdapter):
    name = "nfc"
    kind = "nfc_reader"
    dependency = "nfc"
    real_driver = "nfcpy"

    def __init__(self, config: dict, frontend_factory=None, text_record_factory=None) -> None:
        super().__init__(config)
        self._frontend_factory = frontend_factory
        self._text_record_factory = text_record_factory
        self._io_lock = threading.RLock()
        self._watchers = {}
        self._subscribed: set[str] = set()
        self._tags: dict[str, dict] = {}
        paths = [binding.address["path"] for binding in self.bindings()]
        if len(paths) != len(set(paths)):
            raise ValidationError("bind each NFC reader path only once")

    def available(self) -> bool:
        return (not self._closed and self._frontend_factory is not None) or super().available()

    def connection(self, config: dict) -> dict:
        conn = self.connection_fields(config, {"path"})
        if not conn.get("path"):
            raise ValidationError("NFC connection requires a reader path (for example usb)")
        return conn

    def add_device(self, config: dict):
        with self._io_lock:
            conn = self.connection(config)
            if any(b.address["path"] == conn["path"] for b in self.bindings()):
                raise ValidationError("NFC reader path already bound")
            return super().add_device(config)

    def _frontend(self, path):
        if self._frontend_factory:
            return self._frontend_factory(path)
        import nfc

        return nfc.ContactlessFrontend(path)

    def _tag_data(self, tag) -> dict:
        ndef = tag.ndef
        octets = bytes(ndef.octets) if ndef is not None else b""
        return {
            "uid": bytes(tag.identifier).hex(),
            "ndef_hex": octets[:4096].hex(),
            "ndef_truncated": len(octets) > 4096,
            "writeable": bool(ndef is not None and ndef.is_writeable),
        }

    def _scan(self, binding, text=None, expected_uid=None) -> dict:
        deadline = time.monotonic() + self.timeout
        result = None

        def connected(tag):
            nonlocal result
            if text is not None:
                actual = bytes(tag.identifier).hex()
                if actual != expected_uid:
                    raise ValidationError("NFC UID mismatch; no data written")
                if tag.ndef is None or not tag.ndef.is_writeable:
                    raise ValidationError("tag has no writeable NDEF area")
                factory = self._text_record_factory
                if factory is None:
                    from ndef import TextRecord

                    factory = TextRecord
                tag.ndef.records = [factory(text)]
            result = self._tag_data(tag)
            if text is not None:
                result["written"] = True
            return False  # one operation, do not wait for removal

        frontend = self._frontend(binding.address["path"])
        try:
            frontend.connect(
                rdwr={"on-connect": connected},
                terminate=lambda: self._closed or time.monotonic() >= deadline,
            )
        finally:
            frontend.close()
        if result is None:
            raise AdapterUnavailable("NFC read timed out: no tag detected")
        return result

    def _listen(self, binding, stop, ready, errors) -> None:
        frontend = None
        try:
            frontend = self._frontend(binding.address["path"])
            ready.set()

            def connected(tag):
                if not stop.is_set() and not self._closed:
                    data = self._tag_data(tag)
                    with self._lock:
                        self._tags[binding.device_id] = data
                    self.emit(binding.device_id, "tag", data)
                return True  # nfcpy waits for release; one event per presentation

            while not stop.is_set() and not self._closed:
                frontend.connect(rdwr={"on-connect": connected}, terminate=stop.is_set)
                stop.wait(0.05)
        except Exception as exc:
            errors.append(exc)
            self.emit(binding.device_id, "error", {"operation": "subscribe", "error": str(exc)})
        finally:
            if frontend is not None:
                frontend.close()
            ready.set()

    def _subscribe(self, binding) -> dict:
        device_id = binding.device_id
        existing = self._watchers.get(device_id)
        if existing and existing[1].is_alive():
            return {"subscribed": True}
        self._subscribed.add(device_id)
        if self.driver == "simulated":
            return {"subscribed": True}
        stop, ready, errors = threading.Event(), threading.Event(), []
        thread = threading.Thread(
            target=self._listen,
            args=(binding, stop, ready, errors),
            name=f"southbound-nfc-{device_id}",
            daemon=True,
        )
        self._watchers[device_id] = (stop, thread)
        thread.start()
        if not ready.wait(self.timeout) or errors:
            self._unsubscribe(device_id)
            raise AdapterUnavailable(str(errors[0]) if errors else "NFC reader open timed out")
        return {"subscribed": True}

    def _unsubscribe(self, device_id):
        self._subscribed.discard(device_id)
        watcher = self._watchers.get(device_id)
        if watcher:
            stop, thread = watcher
            stop.set()
            thread.join(timeout=self.timeout + 1)
            if thread.is_alive():
                raise AdapterUnavailable("NFC listener has not stopped; reader remains busy")
            self._watchers.pop(device_id, None)
        return {"subscribed": False}

    def call(self, binding, capability, arguments) -> dict:
        with self._io_lock:
            self.require_available()
            device_id = binding.device_id
            if device_id not in self._devices:
                raise AdapterUnavailable("NFC reader has been removed")
            name = capability.name
            if name == "subscribe":
                result = self._subscribe(binding)
            elif name == "unsubscribe":
                result = self._unsubscribe(device_id)
            else:
                watcher = self._watchers.get(device_id)
                if watcher and watcher[1].is_alive():
                    raise AdapterUnavailable(
                        "NFC reader is listening; unsubscribe before read/write"
                    )
                if name not in ("read", "write_text"):
                    raise ValidationError(f"unsupported NFC capability: {name}")
                uid = _uid(arguments["expected_uid"]) if name == "write_text" else None
                text = arguments["text"] if name == "write_text" else None
                if self.driver == "simulated":
                    if device_id in self._subscribed:
                        raise AdapterUnavailable(
                            "NFC reader is listening; unsubscribe before read/write"
                        )
                    with self._lock:
                        tag = self._tags.setdefault(
                            device_id, _simulated_tag("04aabbcc", "Southbound demo")
                        )
                        if uid is not None:
                            if uid != tag["uid"]:
                                raise ValidationError("NFC UID mismatch; no data written")
                            tag = _simulated_tag(uid, text)
                            self._tags[device_id] = tag
                        result = dict(tag)
                        if uid is not None:
                            result["written"] = True
                else:
                    result = self._scan(binding, text, uid)
            return {**result, "simulated": self.driver == "simulated"}

    def simulate_tag(self, device_id: str, uid: str, text: str = "") -> None:
        """Inject one tag presentation into an explicitly simulated reader."""
        with self._io_lock:
            if self.driver != "simulated" or self._closed or device_id not in self._devices:
                raise ValidationError("a live simulated NFC binding is required")
            data = _simulated_tag(_uid(uid), text)
            with self._lock:
                self._tags[device_id] = data
            if device_id in self._subscribed:
                self.emit(device_id, "tag", data)

    def remove_device(self, device_id: str) -> None:
        with self._io_lock:
            if device_id not in self._devices:
                raise ValidationError("unknown NFC reader")
            self._unsubscribe(device_id)
            with self._lock:
                self._devices.pop(device_id)
                self._tags.pop(device_id, None)

    def close(self) -> None:
        with self._io_lock:
            self._closed = True
            for device_id in list(self._watchers):
                self._unsubscribe(device_id)
            self._subscribed.clear()
