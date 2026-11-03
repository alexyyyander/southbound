"""Camera adapter.

Cameras are read-sensitive devices: a snapshot is a ``read`` capability, but
it exposes the physical world. The adapter keeps snapshots local, records the
evidence reference, and never puts raw frames into the event bus or an agent
prompt.

Sources are deliberately small and lazy:

    synthetic   dependency-free generated frames (demo/tests)
    file        one image file (tests, still cameras)
    usb         OpenCV webcam capture (optional dependency)
    rtsp        one ffmpeg frame grab (optional external binary)

Motion detection lives in ``southbound.vision``; the adapter only owns the
transport and the local evidence store.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import uuid
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from ...paths import DATA_DIR
from ..capabilities import capabilities_for
from ..errors import AdapterUnavailable
from ..models import Capability, DeviceBinding
from .base import DeviceAdapter

SAMPLE_WIDTH = 64
SAMPLE_HEIGHT = 36
DEFAULT_RETENTION_S = 86400
DEFAULT_MAX_SNAPSHOTS = 200
_SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_SLUG = re.compile(r"[^0-9A-Za-z._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Frame:
    """One captured image plus an optional small grayscale sample."""

    data: bytes
    content_type: str
    width: int
    height: int
    gray: bytes | None
    captured_at: str
    simulated: bool = False


def _png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode an RGB byte string as a dependency-free PNG."""

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # no filter
        raw.extend(rgb[y * stride : (y + 1) * stride])

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def _gray_from_rgb(width: int, height: int, rgb: bytes) -> bytes:
    """Box-downsample RGB to the motion detector's small grayscale frame."""

    out = bytearray(SAMPLE_WIDTH * SAMPLE_HEIGHT)
    for gy in range(SAMPLE_HEIGHT):
        y0 = gy * height // SAMPLE_HEIGHT
        y1 = max(y0 + 1, (gy + 1) * height // SAMPLE_HEIGHT)
        for gx in range(SAMPLE_WIDTH):
            x0 = gx * width // SAMPLE_WIDTH
            x1 = max(x0 + 1, (gx + 1) * width // SAMPLE_WIDTH)
            total = 0
            count = 0
            for y in range(y0, y1):
                row = y * width * 3
                for x in range(x0, x1):
                    offset = row + x * 3
                    total += (
                        rgb[offset] * 299
                        + rgb[offset + 1] * 587
                        + rgb[offset + 2] * 114
                    )
                    count += 1
            out[gy * SAMPLE_WIDTH + gx] = total // (count * 1000) if count else 0
    return bytes(out)


def _decode_image(data: bytes) -> tuple[int, int, bytes | None]:
    """Use Pillow when available for size/gray; never make it a hard dependency."""

    try:
        from PIL import Image  # type: ignore
    except Exception:
        return 0, 0, None
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            gray = image.convert("L").resize((SAMPLE_WIDTH, SAMPLE_HEIGHT))
            return width, height, gray.tobytes()
    except Exception:
        return 0, 0, None


def _redact_url(value: str) -> str:
    # ffmpeg stderr may embed the URL in a longer message, so scrub the
    # userinfo pattern first and then handle a bare URL.
    value = re.sub(r"(://)[^/@\s]+@", r"\1***@", value)
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc:
        return value
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"***@{host}" if (parts.username or parts.password) else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class CameraSource(ABC):
    kind = "source"
    simulated = False

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config or {})

    def available(self) -> bool:
        return True

    @abstractmethod
    def snapshot(self) -> Frame:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "available": self.available(),
            "simulated": self.simulated,
        }

    def close(self) -> None:
        """Release resources; safe to call twice."""


class SyntheticCameraSource(CameraSource):
    """Deterministic generated scene with an optional moving block."""

    kind = "synthetic"
    simulated = True

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.width = max(64, int(self.config.get("width") or 160))
        self.height = max(48, int(self.config.get("height") or 90))
        self.motion = bool(self.config.get("motion", True))
        self.cycle_frames = max(1, int(self.config.get("cycle_frames") or 6))
        self._index = 0
        self._position = 0

    def _render(self) -> bytes:
        width, height = self.width, self.height
        rgb = bytearray(width * height * 3)
        for y in range(height):
            for x in range(width):
                offset = (y * width + x) * 3
                rgb[offset] = 18 + (x * 28) // width
                rgb[offset + 1] = 30 + (y * 36) // height
                rgb[offset + 2] = 44 + (x * 30) // width

        # Static window: gives the frame a stable landmark.
        for y in range(10, min(38, height)):
            for x in range(max(0, width - 44), max(0, width - 14)):
                offset = (y * width + x) * 3
                rgb[offset], rgb[offset + 1], rgb[offset + 2] = 92, 126, 150

        # Floor line.
        floor = min(height - 12, height * 3 // 4)
        for y in range(floor, min(floor + 2, height)):
            for x in range(width):
                offset = (y * width + x) * 3
                rgb[offset], rgb[offset + 1], rgb[offset + 2] = 54, 72, 86

        # Moving block (a person proxy), with a head.
        moving_phase = self.motion and (self._index // self.cycle_frames) % 2 == 0
        if moving_phase:
            self._position = (self._position + 7) % max(1, width - 28)
        x0 = 8 + self._position
        y0 = height // 2 - 4
        for y in range(y0, min(y0 + min(34, height // 2), height)):
            for x in range(x0, min(x0 + 16, width)):
                offset = (y * width + x) * 3
                rgb[offset], rgb[offset + 1], rgb[offset + 2] = 214, 116, 70
        for y in range(max(0, y0 - 12), y0):
            for x in range(x0 + 3, min(x0 + 13, width)):
                offset = (y * width + x) * 3
                rgb[offset], rgb[offset + 1], rgb[offset + 2] = 232, 184, 150
        return bytes(rgb)

    def snapshot(self) -> Frame:
        rgb = self._render()
        self._index += 1
        return Frame(
            data=_png(self.width, self.height, rgb),
            content_type="image/png",
            width=self.width,
            height=self.height,
            gray=_gray_from_rgb(self.width, self.height, rgb),
            captured_at=_now(),
            simulated=True,
        )

    def status(self) -> dict[str, Any]:
        return {
            **super().status(),
            "width": self.width,
            "height": self.height,
            "motion": self.motion,
        }


class FileCameraSource(CameraSource):
    """Read one local image file; useful for tests and still cameras."""

    kind = "file"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        path_env = str(self.config.get("path_env") or "")
        self.path = Path(
            (os.environ.get(path_env) if path_env else "")
            or self.config.get("path")
            or ""
        ).expanduser()

    def available(self) -> bool:
        return bool(self.path) and self.path.is_file()

    def snapshot(self) -> Frame:
        if not self.available():
            raise AdapterUnavailable(f"camera file not found: {self.path}")
        suffix = self.path.suffix.lower()
        content_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(suffix)
        if content_type is None:
            raise AdapterUnavailable(f"unsupported camera image type: {suffix}")
        data = self.path.read_bytes()
        width, height, gray = _decode_image(data)
        return Frame(
            data=data,
            content_type=content_type,
            width=width,
            height=height,
            gray=gray,
            captured_at=_now(),
        )


class UsbCameraSource(CameraSource):
    """Local UVC camera through OpenCV (optional ``[vision]`` dependency)."""

    kind = "usb"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.index = int(self.config.get("index") or 0)
        self._capture: Any = None

    def available(self) -> bool:
        return importlib.util.find_spec("cv2") is not None

    def snapshot(self) -> Frame:
        if not self.available():
            raise AdapterUnavailable(
                "OpenCV is not installed; install southbound[vision] for USB cameras"
            )
        import cv2  # type: ignore

        if self._capture is None:
            capture = cv2.VideoCapture(self.index)
            if not capture.isOpened():
                capture.release()
                raise AdapterUnavailable(f"cannot open USB camera {self.index}")
            self._capture = capture
        frame = None
        for _ in range(3):
            ok, frame = self._capture.read()
            if ok:
                break
            time.sleep(0.1)
        if frame is None:
            raise AdapterUnavailable(f"USB camera {self.index} returned no frame")
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise AdapterUnavailable("USB camera JPEG encode failed")
        height, width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (SAMPLE_WIDTH, SAMPLE_HEIGHT))
        return Frame(
            data=encoded.tobytes(),
            content_type="image/jpeg",
            width=int(width),
            height=int(height),
            gray=gray.tobytes(),
            captured_at=_now(),
        )

    def status(self) -> dict[str, Any]:
        return {**super().status(), "index": self.index}

    def close(self) -> None:
        if self._capture is not None:
            try:
                self._capture.release()
            finally:
                self._capture = None


class RtspCameraSource(CameraSource):
    """One-frame RTSP grab through ffmpeg; no stream is kept open."""

    kind = "rtsp"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        url_env = str(self.config.get("url_env") or "")
        self.url = str(
            (os.environ.get(url_env) if url_env else "") or self.config.get("url") or ""
        ).strip()
        self.transport = str(self.config.get("transport") or "tcp")
        self.timeout = max(1.0, float(self.config.get("timeout") or 8))

    def available(self) -> bool:
        return bool(self.url) and shutil.which("ffmpeg") is not None

    def snapshot(self) -> Frame:
        if not self.url:
            raise AdapterUnavailable("RTSP URL is not configured")
        if shutil.which("ffmpeg") is None:
            raise AdapterUnavailable("ffmpeg is required for RTSP snapshots")
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            self.transport,
            "-i",
            self.url,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterUnavailable("RTSP snapshot timed out") from exc
        if completed.returncode != 0 or not completed.stdout:
            detail = (
                completed.stderr.decode("utf-8", "replace").strip().splitlines()[-1]
                if completed.stderr
                else f"ffmpeg exit {completed.returncode}"
            )
            raise AdapterUnavailable(f"RTSP snapshot failed: {_redact_url(detail)}")
        data = completed.stdout
        width, height, gray = _decode_image(data)
        return Frame(
            data=data,
            content_type="image/jpeg",
            width=width,
            height=height,
            gray=gray,
            captured_at=_now(),
        )

    def status(self) -> dict[str, Any]:
        return {
            **super().status(),
            "configured": bool(self.url),
            "url": _redact_url(self.url) if self.url else "",
            "transport": self.transport,
        }


def _source_for(config: dict[str, Any]) -> CameraSource:
    kind = str(config.get("source") or "synthetic").strip().lower()
    if kind == "synthetic":
        return SyntheticCameraSource(config)
    if kind == "file":
        return FileCameraSource(config)
    if kind == "usb":
        return UsbCameraSource(config)
    if kind == "rtsp":
        return RtspCameraSource(config)
    raise ValueError(f"unknown camera source: {kind}")


class CameraAdapter(DeviceAdapter):
    name = "camera"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = dict(config or {})
        self.config = config
        self.snapshot_dir = Path(
            config.get("snapshot_dir") or (DATA_DIR / "vision")
        ).expanduser()
        self.retention_s = max(
            60, int(config.get("retention_s") or DEFAULT_RETENTION_S)
        )
        self.max_snapshots = max(
            1, int(config.get("max_snapshots") or DEFAULT_MAX_SNAPSHOTS)
        )
        self._lock = threading.RLock()
        self._sources: dict[str, CameraSource] = {}
        self._bindings: dict[str, DeviceBinding] = {}
        self._camera_locks: dict[str, threading.Lock] = {}
        self._perception: dict[str, bool] = {}
        self._motion: dict[str, bool] = {}
        self._motion_ratio: dict[str, float] = {}
        self._last: dict[str, dict[str, Any]] = {}
        self._last_frame: dict[str, Frame | None] = {}
        self._evidence_at: dict[str, float] = {}
        self._config_errors: list[dict[str, str]] = []
        for item in config.get("cameras") or []:
            try:
                self.add_device(item)
            except Exception as exc:
                camera_id = item.get("device_id") if isinstance(item, dict) else ""
                self._config_errors.append(
                    {"camera": str(camera_id or ""), "error": str(exc)}
                )

    def available(self) -> bool:
        return True

    def _make_source(self, config: dict[str, Any]) -> CameraSource:
        return _source_for(config)

    def verify_device(self, config: dict[str, Any]) -> None:
        source = self._make_source(config)
        if not source.available():
            detail = source.status()
            raise ValueError(
                f"camera source {source.kind} is not available: "
                f"{detail.get('url') or detail.get('kind')}"
            )

    def add_device(self, config: dict[str, Any]) -> DeviceBinding:
        if not isinstance(config, dict):
            raise ValueError("camera config must be an object")
        device_id = str(config.get("device_id") or config.get("id") or "").strip()
        if not device_id:
            raise ValueError("device_id is required")
        with self._lock:
            if device_id in self._sources:
                raise ValueError(f"camera already exists: {device_id}")
        self.verify_device(config)
        source = self._make_source(config)
        perception = bool(config.get("perception", source.simulated))
        binding = DeviceBinding(
            device_id=device_id,
            name=str(config.get("name") or device_id),
            kind="camera",
            room=str(config.get("room") or ""),
            protocol=str(
                config.get("protocol")
                or {
                    "synthetic": "Synthetic",
                    "file": "File",
                    "usb": "USB",
                    "rtsp": "RTSP",
                }.get(source.kind, source.kind.upper())
            ),
            tier=str(config.get("tier") or "T2"),
            adapter=self.name,
            address={
                key: value
                for key, value in config.items()
                if key in ("source", "path", "url_env", "index")
            },
            capabilities=capabilities_for("camera"),
            metadata={
                "virtual": source.simulated,
                "simulated": source.simulated,
                "perception": perception,
                "privacy": {
                    "camera": True,
                    "retention": f"{self.retention_s}s",
                    "raw_media_stored": True,
                    "faces": "not_processed",
                },
            },
        )
        with self._lock:
            self._sources[device_id] = source
            self._bindings[device_id] = binding
            self._camera_locks[device_id] = threading.Lock()
            self._perception[device_id] = perception
            self._motion[device_id] = False
            self._motion_ratio[device_id] = 0.0
            self._last[device_id] = {}
            self._last_frame[device_id] = None
            self._evidence_at[device_id] = 0.0
        return binding

    def remove_device(self, device_id: str) -> None:
        with self._lock:
            source = self._sources.pop(device_id, None)
            self._bindings.pop(device_id, None)
            self._camera_locks.pop(device_id, None)
            self._perception.pop(device_id, None)
            self._motion.pop(device_id, None)
            self._motion_ratio.pop(device_id, None)
            self._last.pop(device_id, None)
            self._last_frame.pop(device_id, None)
            self._evidence_at.pop(device_id, None)
        if source is not None:
            source.close()

    def bindings(self) -> list[DeviceBinding]:
        with self._lock:
            return list(self._bindings.values())

    def camera_ids(self) -> list[str]:
        with self._lock:
            return list(self._sources)

    def perception_ids(self) -> list[str]:
        with self._lock:
            return [key for key, enabled in self._perception.items() if enabled]

    def is_simulated(self, device_id: str) -> bool:
        with self._lock:
            source = self._sources.get(device_id)
            return bool(source and source.simulated)

    def set_motion(self, device_id: str, motion: bool, ratio: float) -> None:
        with self._lock:
            self._motion[device_id] = bool(motion)
            self._motion_ratio[device_id] = float(ratio)

    def _state(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            source = self._sources[device_id]
            last = dict(self._last.get(device_id) or {})
            return {
                "source": source.kind,
                "available": source.available(),
                "perception": bool(self._perception.get(device_id)),
                "motion": bool(self._motion.get(device_id)),
                "motion_ratio": round(float(self._motion_ratio.get(device_id, 0.0)), 4),
                "last_snapshot": last.get("snapshot", ""),
                "last_snapshot_at": last.get("captured_at", ""),
                "last_snapshot_status": "ok" if last else "",
                "simulated": source.simulated,
            }

    def capture(self, device_id: str) -> Frame:
        with self._lock:
            source = self._sources.get(device_id)
            lock = self._camera_locks.get(device_id)
        if source is None or lock is None:
            raise AdapterUnavailable(f"unknown camera: {device_id}")
        with lock:
            frame = source.snapshot()
        with self._lock:
            self._last_frame[device_id] = frame
        return frame

    def last_frame(self, device_id: str) -> Frame | None:
        with self._lock:
            return self._last_frame.get(device_id)

    def last_snapshot(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._last.get(device_id) or {})

    def _camera_dir(self, device_id: str) -> Path:
        slug = _SAFE_SLUG.sub("_", device_id).strip("_") or "camera"
        digest = hashlib.sha1(device_id.encode("utf-8")).hexdigest()[:8]
        return self.snapshot_dir / f"{slug}-{digest}"

    def store_frame(self, device_id: str, frame: Frame) -> dict[str, Any]:
        suffix = ".png" if frame.content_type == "image/png" else ".jpg"
        directory = self._camera_dir(device_id)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        filename = f"{stamp}-{uuid.uuid4().hex[:8]}{suffix}"
        path = directory / filename
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(frame.data)
        temporary.replace(path)
        evidence = {
            "snapshot": f"local://vision/{quote(device_id, safe='')}/{filename}",
            "url": f"/api/vision/snapshots/{quote(device_id, safe='')}/{filename}",
            "sha256": hashlib.sha256(frame.data).hexdigest(),
            "content_type": frame.content_type,
            "width": frame.width,
            "height": frame.height,
            "captured_at": frame.captured_at,
        }
        with self._lock:
            self._last[device_id] = evidence
            self._evidence_at[device_id] = time.time()
        self._prune(directory)
        return evidence

    def store_latest(self, device_id: str, frame: Frame) -> dict[str, Any]:
        with self._lock:
            last = time.time() - float(self._evidence_at.get(device_id, 0.0))
        if last < 30:
            return dict(self._last.get(device_id) or {})
        return self.store_frame(device_id, frame)

    def _prune(self, directory: Path) -> None:
        cutoff = time.time() - self.retention_s
        try:
            files = sorted(
                (path for path in directory.iterdir() if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for index, path in enumerate(files):
            try:
                if index >= self.max_snapshots or path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def snapshot_path(self, device_id: str, filename: str) -> Path | None:
        if not _SAFE_FILE.match(filename) or not filename.lower().endswith(
            (".png", ".jpg", ".jpeg")
        ):
            return None
        with self._lock:
            if device_id not in self._sources:
                return None
        directory = self._camera_dir(device_id).resolve()
        candidate = (directory / filename).resolve()
        try:
            candidate.relative_to(directory)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def call(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if capability.name == "read":
            return {"state": self._state(binding.device_id)}
        if capability.name == "snapshot":
            frame = self.capture(binding.device_id)
            evidence = self.store_frame(binding.device_id, frame)
            return {
                "state": self._state(binding.device_id),
                "snapshot": evidence,
            }
        raise AdapterUnavailable(f"camera does not support: {capability.name}")

    def status(self) -> dict[str, Any]:
        with self._lock:
            cameras = []
            for device_id, binding in self._bindings.items():
                source = self._sources[device_id]
                cameras.append(
                    {
                        "device_id": device_id,
                        "name": binding.name,
                        "room": binding.room,
                        "protocol": binding.protocol,
                        "source": source.kind,
                        "available": source.available(),
                        "perception": bool(self._perception.get(device_id)),
                        "motion": bool(self._motion.get(device_id)),
                        "motion_ratio": round(
                            float(self._motion_ratio.get(device_id, 0.0)), 4
                        ),
                        "last_snapshot": dict(self._last.get(device_id) or {}),
                        "last_snapshot_status": (
                            "ok" if self._last.get(device_id) else ""
                        ),
                        "privacy": dict(binding.metadata.get("privacy") or {}),
                        "simulated": source.simulated,
                    }
                )
        return {
            "ok": True,
            "adapter": self.name,
            "available": True,
            "storage": "local",
            "retention_s": self.retention_s,
            "max_snapshots": self.max_snapshots,
            "cameras": cameras,
            "errors": list(self._config_errors),
        }

    def close(self) -> None:
        with self._lock:
            sources = list(self._sources.values())
            self._sources.clear()
            self._bindings.clear()
            self._camera_locks.clear()
        for source in sources:
            try:
                source.close()
            except Exception:
                pass
