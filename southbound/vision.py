"""Local perception plane.

The camera adapter answers "what is this camera"; this module answers "did
something change in front of it". It samples configured cameras, compares
small grayscale frames, and publishes a structured observation event on
motion transitions. Raw frames stay in the local evidence store; the event
bus carries only metadata and a local snapshot reference.

The first detector is deliberately simple and dependency-free. A model-backed
detector can replace ``MotionDetector`` later without changing the event or
observation contract.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .access.adapters.camera import SAMPLE_HEIGHT, SAMPLE_WIDTH, Frame

DEFAULT_INTERVAL_S = 1.5
DEFAULT_THRESHOLD = 0.02
DEFAULT_PIXEL_DELTA = 18


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class MotionResult:
    available: bool
    motion: bool = False
    ratio: float = 0.0
    changed: bool = False
    previous: bool = False


class MotionDetector:
    """Frame-difference motion detector over a small grayscale sample."""

    def __init__(
        self,
        width: int = SAMPLE_WIDTH,
        height: int = SAMPLE_HEIGHT,
        threshold: float = DEFAULT_THRESHOLD,
        pixel_delta: int = DEFAULT_PIXEL_DELTA,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.threshold = max(0.0, float(threshold))
        self.pixel_delta = max(1, int(pixel_delta))
        self._previous: bytes | None = None
        self._motion = False

    @property
    def expected_size(self) -> int:
        return self.width * self.height

    def reset(self) -> None:
        self._previous = None
        self._motion = False

    def update(self, gray: bytes | None) -> MotionResult:
        if not gray or len(gray) != self.expected_size:
            self.reset()
            return MotionResult(available=False)
        if self._previous is None:
            self._previous = gray
            return MotionResult(available=True)
        changed = sum(
            1
            for left, right in zip(gray, self._previous)
            if abs(left - right) >= self.pixel_delta
        )
        ratio = changed / len(gray)
        motion = ratio >= self.threshold
        state_changed = motion != self._motion
        previous = self._motion
        self._motion = motion
        self._previous = gray
        return MotionResult(
            available=True,
            motion=motion,
            ratio=ratio,
            changed=state_changed,
            previous=previous,
        )


class PerceptionService:
    """Sample camera sources and publish ``vision.*`` events on transitions."""

    def __init__(
        self,
        runtime: Any,
        adapter_name: str = "camera",
        interval: float = DEFAULT_INTERVAL_S,
        threshold: float = DEFAULT_THRESHOLD,
        pixel_delta: int = DEFAULT_PIXEL_DELTA,
        start: bool = False,
    ) -> None:
        self.runtime = runtime
        self.adapter_name = adapter_name
        self.interval = max(0.5, float(interval))
        self.threshold = max(0.0, float(threshold))
        self.pixel_delta = max(1, int(pixel_delta))
        self._lock = threading.RLock()
        self._detectors: dict[str, MotionDetector] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ticks = 0
        self._events = 0
        self._last_tick = ""
        self._last_error = ""
        self._started_at = ""
        if start:
            self.start()

    def _adapter(self) -> Any | None:
        hub = getattr(self.runtime, "hub", None)
        adapter = hub.adapters.get(self.adapter_name) if hub is not None else None
        if adapter is None or not callable(getattr(adapter, "perception_ids", None)):
            return None
        return adapter

    def camera_ids(self) -> list[str]:
        adapter = self._adapter()
        return list(adapter.perception_ids()) if adapter is not None else []

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None:
                return self.status()
            self._stop.clear()
            self._started_at = _now()
            self._thread = threading.Thread(
                target=self._loop,
                name="southbound-perception",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        with self._lock:
            self._thread = None
            self._detectors.clear()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception as exc:  # keep the perception thread alive
                self._last_error = f"{type(exc).__name__}: {exc}"

    def tick(self) -> dict[str, Any]:
        adapter = self._adapter()
        if adapter is None:
            return {
                "ok": False,
                "error": "camera adapter not enabled",
                "cameras": {},
            }
        results: dict[str, Any] = {}
        active = set(adapter.perception_ids())
        for stale in set(self._detectors) - active:
            self._detectors.pop(stale, None)
        for camera_id in adapter.perception_ids():
            detector = self._detectors.get(camera_id)
            if detector is None:
                detector = MotionDetector(
                    threshold=self.threshold,
                    pixel_delta=self.pixel_delta,
                )
                self._detectors[camera_id] = detector
            try:
                frame = adapter.capture(camera_id)
            except Exception as exc:
                adapter.set_motion(camera_id, False, 0.0)
                results[camera_id] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                continue
            result = detector.update(frame.gray)
            adapter.set_motion(camera_id, result.motion, result.ratio)
            if not result.available:
                results[camera_id] = {
                    "ok": False,
                    "error": "frame has no grayscale sample (install southbound[vision])",
                }
                continue
            results[camera_id] = {
                "ok": True,
                "motion": result.motion,
                "ratio": round(result.ratio, 4),
                "changed": result.changed,
            }
            if result.changed:
                self._publish(camera_id, adapter, frame, result)
            elif not adapter.last_snapshot(camera_id):
                # Give the dashboard one local frame without waiting for motion.
                adapter.store_frame(camera_id, frame)
        self._ticks += 1
        self._last_tick = _now()
        return {"ok": True, "cameras": results}

    def _publish(
        self,
        camera_id: str,
        adapter: Any,
        frame: Frame,
        result: MotionResult,
    ) -> None:
        evidence = adapter.store_latest(camera_id, frame) if result.motion else {}
        try:
            binding = self.runtime.hub.describe(camera_id)
        except Exception:
            binding = {"name": camera_id, "room": ""}
        event_type = "vision.motion" if result.motion else "vision.motion_stopped"
        confidence = (
            round(min(0.99, 0.55 + result.ratio * 4), 2) if result.motion else 0.9
        )
        payload = {
            "type": event_type,
            "plane": "northbound",
            "direction": "up",
            "source": "perception",
            "device_id": camera_id,
            "name": binding.get("name") or camera_id,
            "zone": binding.get("room") or "",
            "confidence": confidence,
            "data": {
                "motion": result.motion,
                "ratio": round(result.ratio, 4),
                "threshold": self.threshold,
                "frame": evidence.get("snapshot", ""),
            },
            "evidence": evidence,
            "privacy": {
                "camera": True,
                "retention": f"{adapter.retention_s}s",
                "raw_media_stored": bool(evidence),
            },
            "simulated": adapter.is_simulated(camera_id),
            "ok": True,
        }
        events = getattr(self.runtime, "events", None)
        if events is not None:
            events.publish(payload)
            self._events += 1

    def status(self) -> dict[str, Any]:
        adapter = self._adapter()
        with self._lock:
            return {
                "ok": adapter is not None,
                "running": self._thread is not None,
                "adapter": self.adapter_name,
                "interval_s": self.interval,
                "threshold": self.threshold,
                "pixel_delta": self.pixel_delta,
                "ticks": self._ticks,
                "events": self._events,
                "last_tick": self._last_tick,
                "last_error": self._last_error,
                "started_at": self._started_at,
                "cameras": adapter.perception_ids() if adapter is not None else [],
                "detectors": sorted(self._detectors),
            }

    def close(self) -> None:
        self.stop()
