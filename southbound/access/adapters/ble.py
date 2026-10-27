"""BLE GATT access with a persistent asyncio loop and explicit characteristic bindings."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from uuid import UUID

from ..errors import AdapterUnavailable, ValidationError
from .hardware import HardwareAdapter


def _characteristic(value: str) -> str:
    try:
        UUID(value)
    except ValueError as exc:
        raise ValidationError("GATT characteristics require a full 128-bit UUID") from exc
    return value.lower()


class BleAdapter(HardwareAdapter):
    name = "ble"
    kind = "ble_device"
    dependency = "bleak"
    real_driver = "bleak"

    def __init__(self, config: dict, client_factory=None) -> None:
        super().__init__(config)
        self._client_factory = client_factory
        self._io_lock = threading.RLock()
        self._loop = None
        self._thread = None
        self._clients = {}
        self._subscribed: set[str] = set()
        self._notifying: set[str] = set()
        self._values: dict[str, bytes] = {}
        self._async_lock = None

    def available(self) -> bool:
        return (not self._closed and self._client_factory is not None) or super().available()

    def connection(self, config: dict) -> dict:
        conn = self.connection_fields(config, {"address", "read_char", "write_char", "notify_char"})
        if not conn.get("address") or not conn.get("read_char"):
            raise ValidationError("BLE connection requires address and read_char")
        for key in ("read_char", "write_char", "notify_char"):
            if key in conn:
                conn[key] = _characteristic(conn[key])
        return conn

    def supported_capabilities(self, connection: dict) -> set[str]:
        caps = {"read"}
        if connection.get("write_char"):
            caps.add("write")
        if connection.get("notify_char"):
            caps.update(("subscribe", "unsubscribe"))
        return caps

    def _run(self, coroutine):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            loop = self._loop

            def worker():
                asyncio.set_event_loop(loop)
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

            self._thread = threading.Thread(target=worker, name="southbound-ble", daemon=True)
            self._thread.start()
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=self.timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise AdapterUnavailable(
                "BLE operation timed out; write outcome may be unknown"
            ) from exc

    def _notification(self, device_id: str, data: bytes) -> None:
        if device_id in self._subscribed and device_id in self._devices and not self._closed:
            self.emit(device_id, "notification", {"hex": bytes(data).hex()})

    async def _client(self, binding):
        device_id = binding.device_id
        client = self._clients.get(device_id)
        if client is None or not client.is_connected:
            if self._client_factory is None:
                from bleak import BleakClient

                factory = BleakClient
            else:
                factory = self._client_factory

            def disconnected(disconnected_client):
                if self._clients.get(device_id) is disconnected_client:
                    self._notifying.discard(device_id)
                    self.emit(device_id, "connection", {"connected": False})

            client = factory(
                binding.address["address"], timeout=self.timeout, disconnected_callback=disconnected
            )
            self._clients[device_id] = client
            try:
                await client.connect()
            except BaseException:
                try:
                    await client.disconnect()
                finally:
                    self._clients.pop(device_id, None)
                raise
            self.emit(device_id, "connection", {"connected": True})
        if device_id in self._subscribed and device_id not in self._notifying:
            await client.start_notify(
                binding.address["notify_char"], lambda _, data: self._notification(device_id, data)
            )
            self._notifying.add(device_id)
        return client

    async def _execute(self, binding, name, data):
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        async with self._async_lock:
            return await self._execute_locked(binding, name, data)

    async def _execute_locked(self, binding, name, data):
        device_id = binding.device_id
        if name == "unsubscribe":
            self._subscribed.discard(device_id)
            client = self._clients.get(device_id)
            if client is not None and client.is_connected and device_id in self._notifying:
                await client.stop_notify(binding.address["notify_char"])
            self._notifying.discard(device_id)
            return {"subscribed": False}
        client = await self._client(binding)
        if name == "read":
            value = await client.read_gatt_char(binding.address["read_char"])
            return {"hex": bytes(value).hex()}
        if name == "write":
            await client.write_gatt_char(binding.address["write_char"], data, response=True)
            return {"written_hex": data.hex()}
        if name == "subscribe":
            self._subscribed.add(device_id)
            try:
                await self._client(binding)
            except BaseException:
                self._subscribed.discard(device_id)
                raise
            return {"subscribed": True}
        raise ValidationError(f"unsupported BLE capability: {name}")

    def call(self, binding, capability, arguments) -> dict:
        with self._io_lock:
            self.require_available()
            if binding.device_id not in self._devices:
                raise AdapterUnavailable("BLE device has been removed")
            name = capability.name
            data = b""
            if name == "write":
                try:
                    data = bytes.fromhex(arguments["hex"])
                except ValueError as exc:
                    raise ValidationError("hex must contain hexadecimal bytes") from exc
                if not data or len(data) > 512:
                    raise ValidationError("BLE writes require 1–512 bytes")
            try:
                if self.driver == "simulated":
                    result = self._simulate(binding.device_id, name, data)
                else:
                    result = self._run(self._execute(binding, name, data))
            except Exception as exc:
                self.emit(binding.device_id, "error", {"operation": name, "error": str(exc)})
                raise
            return {**result, "simulated": self.driver == "simulated"}

    def _simulate(self, device_id, name, data) -> dict:
        if name == "read":
            return {"hex": self._values.get(device_id, b"\x00").hex()}
        if name == "write":
            self._values[device_id] = data
            self._notification(device_id, data)
            return {"written_hex": data.hex()}
        if name == "subscribe":
            self._subscribed.add(device_id)
            return {"subscribed": True}
        if name == "unsubscribe":
            self._subscribed.discard(device_id)
            return {"subscribed": False}
        raise ValidationError(f"unsupported BLE capability: {name}")

    def simulate_notification(self, device_id: str, data: bytes) -> None:
        """In-process test/demo hook; never exposed as a real hardware tool."""
        with self._io_lock:
            if self.driver != "simulated" or self._closed or device_id not in self._devices:
                raise ValidationError("a live simulated BLE binding is required")
            self._values[device_id] = bytes(data)
            self._notification(device_id, data)

    async def _disconnect(self, device_id):
        self._subscribed.discard(device_id)
        self._notifying.discard(device_id)
        client = self._clients.pop(device_id, None)
        if client is not None:
            await client.disconnect()

    def remove_device(self, device_id: str) -> None:
        with self._io_lock:
            if device_id not in self._devices:
                raise ValidationError("unknown BLE device")
            if self._loop is not None:
                self._run(self._disconnect(device_id))
            self._subscribed.discard(device_id)
            self._values.pop(device_id, None)
            with self._lock:
                self._devices.pop(device_id)

    async def _disconnect_all(self):
        await asyncio.gather(
            *(self._disconnect(key) for key in list(self._clients)), return_exceptions=True
        )

    def close(self) -> None:
        with self._io_lock:
            if self._closed:
                return
            self._closed = True
            self._subscribed.clear()
            if self._loop is not None:
                try:
                    self._run(self._disconnect_all())
                finally:
                    self._loop.call_soon_threadsafe(self._loop.stop)
                    self._thread.join(timeout=self.timeout + 1)
