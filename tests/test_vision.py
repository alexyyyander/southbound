import tempfile
import unittest
from pathlib import Path

from southbound.access import AccessHub, AuditLog, PolicyConfig, PolicyEngine
from southbound.access.adapters.camera import (
    SAMPLE_HEIGHT,
    SAMPLE_WIDTH,
    CameraAdapter,
    FileCameraSource,
    SyntheticCameraSource,
)
from southbound.access.errors import PolicyDenied
from southbound.observations import ObservationService, ObservationStore
from southbound.runtime import EventBus, SouthboundRuntime
from southbound.vision import MotionDetector, PerceptionService

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_runtime(
    tmp: str, cameras: list[dict]
) -> tuple[SouthboundRuntime, CameraAdapter]:
    adapter = CameraAdapter(
        {
            "snapshot_dir": str(Path(tmp) / "vision"),
            "cameras": cameras,
        }
    )
    hub = AccessHub(
        [adapter],
        PolicyEngine(PolicyConfig()),
        AuditLog(Path(tmp) / "audit.jsonl"),
    )
    runtime = SouthboundRuntime(hub=hub, events=EventBus())
    return runtime, adapter


class SyntheticCameraTest(unittest.TestCase):
    def test_snapshot_is_a_png_with_a_gray_sample(self) -> None:
        source = SyntheticCameraSource({})
        frame = source.snapshot()
        self.assertTrue(frame.data.startswith(PNG_MAGIC))
        self.assertEqual((frame.width, frame.height), (160, 90))
        self.assertEqual(len(frame.gray or b""), SAMPLE_WIDTH * SAMPLE_HEIGHT)
        self.assertTrue(frame.simulated)

    def test_moving_frames_differ_and_static_frames_match(self) -> None:
        moving = SyntheticCameraSource({})
        first = moving.snapshot()
        second = moving.snapshot()
        self.assertNotEqual(first.gray, second.gray)

        static = SyntheticCameraSource({"motion": False})
        self.assertEqual(static.snapshot().gray, static.snapshot().gray)

    def test_file_source_reads_a_png(self) -> None:
        frame = SyntheticCameraSource({}).snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.png"
            path.write_bytes(frame.data)
            source = FileCameraSource({"path": str(path)})
            loaded = source.snapshot()
            self.assertEqual(loaded.content_type, "image/png")
            self.assertEqual(loaded.data, frame.data)
            self.assertTrue(source.available())


class MotionDetectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.size = SAMPLE_WIDTH * SAMPLE_HEIGHT
        self.base = bytes([12] * self.size)

    def test_motion_state_transitions(self) -> None:
        detector = MotionDetector()
        first = detector.update(self.base)
        self.assertTrue(first.available)
        self.assertFalse(first.motion)
        self.assertFalse(first.changed)

        changed = bytearray(self.base)
        changed[:400] = bytes([220] * 400)
        motion = detector.update(bytes(changed))
        self.assertTrue(motion.motion)
        self.assertTrue(motion.changed)
        self.assertGreater(motion.ratio, 0.02)

        changed_again = bytearray(changed)
        changed_again[400:800] = bytes([220] * 400)
        still_moving = detector.update(bytes(changed_again))
        self.assertTrue(still_moving.motion)
        self.assertFalse(still_moving.changed)

        stopped = detector.update(bytes(changed_again))
        self.assertFalse(stopped.motion)
        self.assertTrue(stopped.changed)

    def test_missing_gray_sample_is_unavailable(self) -> None:
        result = MotionDetector().update(None)
        self.assertFalse(result.available)


class CameraAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime, self.adapter = make_runtime(
            self.tmp.name,
            [
                {
                    "device_id": "entry_camera",
                    "name": "入户摄像头",
                    "room": "玄关",
                    "source": "synthetic",
                    "perception": True,
                }
            ],
        )

    def tearDown(self) -> None:
        self.runtime.close()
        self.tmp.cleanup()

    def test_binding_exposes_camera_capabilities(self) -> None:
        binding = self.adapter.bindings()[0]
        self.assertEqual(binding.kind, "camera")
        self.assertEqual(
            {cap.name for cap in binding.capabilities}, {"snapshot", "read"}
        )
        self.assertTrue(binding.metadata["simulated"])

    def test_snapshot_and_read_go_through_access_hub(self) -> None:
        result = self.runtime.call("entry_camera", "snapshot")
        evidence = result["result"]["snapshot"]
        self.assertTrue(evidence["snapshot"].startswith("local://vision/"))
        self.assertEqual(evidence["content_type"], "image/png")
        filename = evidence["url"].rsplit("/", 1)[-1]
        path = self.adapter.snapshot_path("entry_camera", filename)
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(path.read_bytes().startswith(PNG_MAGIC))

        state = self.runtime.call("entry_camera", "read")["result"]["state"]
        self.assertEqual(state["source"], "synthetic")
        self.assertEqual(state["last_snapshot"], evidence["snapshot"])

    def test_snapshot_path_rejects_traversal(self) -> None:
        result = self.runtime.call("entry_camera", "snapshot")
        filename = result["result"]["snapshot"]["url"].rsplit("/", 1)[-1]
        self.assertIsNone(self.adapter.snapshot_path("entry_camera", "../x.png"))
        self.assertIsNone(self.adapter.snapshot_path("entry_camera", ".."))
        self.assertIsNone(self.adapter.snapshot_path("missing", filename))

    def test_duplicate_and_unknown_sources_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.add_device(
                {"device_id": "entry_camera", "source": "synthetic"}
            )
        with self.assertRaises(ValueError):
            self.adapter.add_device({"device_id": "bad_camera", "source": "unknown"})

    def test_camera_policy_gate_is_separate_from_risk(self) -> None:
        adapter = CameraAdapter(
            {
                "snapshot_dir": str(Path(self.tmp.name) / "vision"),
                "cameras": [{"device_id": "cam", "source": "synthetic"}],
            }
        )
        hub = AccessHub(
            [adapter],
            PolicyEngine(PolicyConfig(allow_camera=False)),
            AuditLog(Path(self.tmp.name) / "audit.jsonl"),
        )
        try:
            with self.assertRaises(PolicyDenied):
                hub.call("cam", "snapshot")
        finally:
            hub.close()

        read_only = AccessHub(
            [
                CameraAdapter(
                    {
                        "snapshot_dir": str(Path(self.tmp.name) / "vision"),
                        "cameras": [{"device_id": "cam2", "source": "synthetic"}],
                    }
                )
            ],
            PolicyEngine(PolicyConfig(mode="read_only")),
            AuditLog(Path(self.tmp.name) / "audit.jsonl"),
        )
        try:
            self.assertTrue(read_only.call("cam2", "snapshot")["ok"])
        finally:
            read_only.close()


class PerceptionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime, self.adapter = make_runtime(
            self.tmp.name,
            [
                {
                    "device_id": "entry_camera",
                    "name": "入户摄像头",
                    "room": "玄关",
                    "source": "synthetic",
                    "perception": True,
                }
            ],
        )
        self.observations = ObservationService(
            self.runtime,
            ObservationStore(Path(self.tmp.name) / "observations.sqlite3"),
        )
        self.perception = PerceptionService(self.runtime)

    def tearDown(self) -> None:
        self.perception.stop()
        self.observations.close()
        self.runtime.close()
        self.tmp.cleanup()

    def test_motion_events_become_vision_observations(self) -> None:
        for _ in range(4):
            result = self.perception.tick()
            self.assertTrue(result["cameras"]["entry_camera"]["ok"])
        events = [
            event
            for event in self.runtime.recent_events()
            if str(event.get("type", "")).startswith("vision.")
        ]
        self.assertTrue(events)
        for event in events:
            self.observations.record_event(event)
        rows = self.observations.observations()
        self.assertEqual(rows[0]["type"], "vision.motion")
        self.assertEqual(rows[0]["modality"], "vision")
        self.assertEqual(rows[0]["device_id"], "entry_camera")
        self.assertEqual(rows[0]["zone"], "玄关")
        self.assertTrue(rows[0]["evidence"]["snapshot"].startswith("local://vision/"))

    def test_status_reports_the_running_surface(self) -> None:
        self.perception.tick()
        status = self.perception.status()
        self.assertTrue(status["ok"])
        self.assertFalse(status["running"])
        self.assertEqual(status["cameras"], ["entry_camera"])
        self.assertEqual(status["ticks"], 1)


class McpVisionSurfaceTest(unittest.TestCase):
    def test_vision_tools_are_exposed(self) -> None:
        from southbound import mcp_server

        tools = mcp_server.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertIn("iot_vision_status", names)
        self.assertIn("iot_vision_snapshot", names)
        self.assertIn("iot_vision_observe", names)
        self.assertEqual(len(tools), 31)


if __name__ == "__main__":
    unittest.main()
