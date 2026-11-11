from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from southbound.access.adapters.endpoint import EndpointAdapter
from southbound.access.adapters.sim import SimAdapter
from southbound.access.errors import AdapterUnavailable
from southbound.access.hub import AccessHub
from southbound.missions import MissionError, MissionService, MissionStore
from southbound.runtime import SouthboundRuntime
from southbound.simulator import Simulator
from southbound.terminal_service import TerminalService
from southbound.terminals import TerminalConflict, TerminalStore


class Crash(BaseException):
    """A process exit bypasses exception handlers and leaves a durable checkpoint."""


class MissionWorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.db = Path(self.tmp.name) / "missions.sqlite3"
        self.terminals = TerminalStore(Path(self.tmp.name) / "terminals.sqlite3", clock=lambda: self.now)
        sim = Simulator()
        self.runtime = SouthboundRuntime(hub=AccessHub([EndpointAdapter(store=self.terminals), SimAdapter(sim)]), simulator=sim)
        self.addCleanup(self.runtime.close)
        self.service = MissionService(self.runtime, store=MissionStore(self.db))
        self.service.worker.clock = lambda: self.now
        self.addCleanup(self.service.close)
        self.terminal_service = TerminalService(self.runtime)
        p = self.terminal_service.create_pairing({"device_id": "phone", "capabilities": ["notify", "speak", "read_battery"], "confirm": True})
        self.token = self.terminal_service.pair({"protocol_version": 1, "pairing_code": p["pairing_code"],
                                               "capabilities": {"notify": 1, "speak": 1, "read_battery": 1}})["token"]

    def create(self, actions=None, **kwargs):
        mission = self.service.create("phone task", actions or [
            {"device_id": "phone", "capability": "notify", "arguments": {"title": "Reminder", "text": "Water"}},
            {"device_id": "phone", "capability": "speak", "arguments": {"text": "Water"}},
        ], requires_approval=False, **kwargs)
        self.service.run(mission["mission_id"])
        return mission["mission_id"]

    def tick(self, seconds=1):
        self.now += seconds
        self.service.worker.tick()

    def complete_phone(self, status="succeeded", result=None):
        command = self.terminal_service.poll(self.token)["command"]
        self.assertIsNotNone(command)
        if result is None:
            result = {"published": True} if command["capability"] == "notify" else {"completed": True}
        self.terminal_service.receipt(self.token, {"command_id": command["command_id"], "status": status, "result": result})
        return command

    def restart(self):
        self.service.close()
        self.service = MissionService(self.runtime, store=MissionStore(self.db))
        self.service.worker.clock = lambda: self.now
        self.addCleanup(self.service.close)

    def test_sequential_receipts_and_completed_is_not_verified(self):
        mid = self.create()
        self.tick()
        self.assertEqual(self.service.get(mid)["result"]["phase"], "waiting")
        self.tick()
        self.assertEqual(self.terminals.db.execute("SELECT COUNT(*) FROM terminal_commands").fetchone()[0], 1)
        self.complete_phone()
        self.tick()
        self.assertEqual(self.service.get(mid)["result"]["next_action"], 1)
        self.tick()
        self.complete_phone()
        self.tick()
        self.tick()
        self.assertEqual(self.service.get(mid)["status"], "completed")
        self.assertEqual(len(self.service.get(mid)["result"]["actions"]), 2)

    def test_wait_for_phone_user_is_durable_and_does_not_advance(self):
        mid = self.create()
        self.tick()
        command = self.terminal_service.poll(self.token)['command']
        cid = command['command_id']
        self.terminal_service.progress(self.token, {'command_id': cid, 'phase': 'waiting_for_user', 'message': 'Please confirm on phone'})
        self.tick()
        self.assertEqual(self.service.get(mid)['result']['phase'], 'waiting_for_user')
        self.assertEqual(self.service.get(mid)['result']['next_action'], 0)
        self.restart()
        self.tick()
        self.assertEqual(self.service.get(mid)['result']['phase'], 'waiting_for_user')
        self.terminal_service.progress(self.token, {'command_id': cid, 'phase': 'executing'})
        self.tick()
        self.assertEqual(self.service.get(mid)['result']['phase'], 'waiting')
        self.terminal_service.receipt(self.token, {'command_id': cid, 'status': 'succeeded', 'result': {'published': True}})
        self.tick()
        self.assertEqual(self.service.get(mid)['result']['next_action'], 1)

    def test_delay_deadline_and_restart(self):
        mid = self.create(delay_s=30, timeout_s=10)
        self.tick(20)
        self.assertEqual(self.terminals.db.execute("SELECT COUNT(*) FROM terminal_commands").fetchone()[0], 0)
        self.restart()
        self.tick(11)
        command_id = self.service.get(mid)["result"]["command_id"]
        self.tick(10)
        self.assertEqual(self.service.get(mid)["status"], "failed")
        self.assertEqual(self.terminals.command("phone", command_id)["status"], "cancelled")

    def test_terminal_receipt_verifies_fresh_result(self):
        mid = self.create(actions=[{"device_id": "phone", "capability": "read_battery"}],
                          verify={"device_id": "phone", "field": "level", "op": ">", "value": 20})
        self.tick()
        self.complete_phone(result={"level": 70, "charging": False})
        self.tick()
        self.tick()
        result = self.service.get(mid)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["result"]["verification"][0]["source"], "terminal_receipt")

    def test_receipt_failure_and_unknown_do_not_advance(self):
        for status in ("failed", "unknown"):
            mid = self.create()
            self.tick()
            self.complete_phone(status=status, result={"error": "uncertain device state"})
            self.tick()
            self.assertEqual(self.service.get(mid)["status"], "failed")
            self.assertEqual(self.service.get(mid)["result"]["next_action"], 0)

    def test_expired_terminal_command_fails_mission(self):
        mid = self.create()
        self.tick()
        self.tick(121)
        self.assertEqual(self.service.get(mid)["status"], "failed")
        self.assertIsNone(self.terminal_service.poll(self.token)["command"])

    def test_cancel_while_waiting_cancels_delivery(self):
        mid = self.create()
        self.tick()
        command = self.service.get(mid)["result"]["command_id"]
        self.service.cancel(mid)
        self.assertEqual(self.terminals.command("phone", command)["status"], "cancelled")
        self.tick()
        self.assertEqual(self.service.get(mid)["status"], "cancelled")
        self.assertIsNone(self.terminal_service.poll(self.token)["command"])

    def test_cancel_leased_command_rejects_late_success(self):
        mid = self.create()
        self.tick()
        command = self.terminal_service.poll(self.token)["command"]
        self.service.cancel(mid)
        with self.assertRaises(TerminalConflict):
            self.terminal_service.receipt(self.token, {"command_id": command["command_id"], "status": "succeeded", "result": {"published": True}})
        self.assertEqual(self.service.get(mid)["status"], "cancelled")

    def test_unavailable_adapter_does_not_block_other_jobs_and_cleanup_recovers(self):
        mid = self.create()
        self.tick()
        command = self.service.get(mid)["result"]["command_id"]
        adapter = self.runtime.hub.adapters.pop("endpoint")
        try:
            other = self.create(actions=[{"device_id": "living_light", "capability": "turn_on"}])
            self.tick()
            self.tick()
            self.assertEqual(self.service.get(other)["status"], "completed")
            self.assertEqual(self.service.get(mid)["status"], "failed")
            self.assertTrue(self.service.get(mid)["result"]["cancellation_pending"])
        finally:
            self.runtime.hub.adapters["endpoint"] = adapter
        self.restart()
        self.tick()
        self.assertNotIn("cancellation_pending", self.service.get(mid)["result"])
        self.assertEqual(self.terminals.command("phone", command)["status"], "cancelled")

    def test_cancel_cleanup_recovers_after_restart(self):
        mid = self.create()
        self.tick()
        command = self.service.get(mid)["result"]["command_id"]
        # Simulate process loss between durable cancellation and queue cleanup.
        self.service.store.update(mid, status="cancelled")
        self.restart()
        self.tick()
        self.assertEqual(self.service.get(mid)["result"]["phase"], "cancelled")
        self.assertEqual(self.terminals.command("phone", command)["status"], "cancelled")

    def test_crash_after_enqueue_before_checkpoint_reuses_command(self):
        mid = self.create()
        original = self.service.worker._save
        def crash(job, progress, *args, **kwargs):
            if progress["phase"] == "waiting":
                raise Crash()
            return original(job, progress, *args, **kwargs)
        with patch.object(self.service.worker, "_save", side_effect=crash):
            with self.assertRaises(Crash):
                self.tick()
        command_id = self.terminals.db.execute("SELECT command_id FROM terminal_commands").fetchone()[0]
        self.restart()
        self.tick(61)
        self.assertEqual(self.service.get(mid)["result"]["command_id"], command_id)
        self.assertEqual(self.terminals.db.execute("SELECT COUNT(*) FROM terminal_commands").fetchone()[0], 1)

    def test_cancel_dispatch_checkpoint_prevents_future_enqueue(self):
        mid = self.create()
        with patch.object(self.runtime, "call", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.tick()
        key = self.service.get(mid)["result"]["idempotency_key"]
        self.service.cancel(mid)
        with self.assertRaises(AdapterUnavailable):
            self.runtime.call("phone", "notify", {"title": "Reminder", "text": "Water"}, idempotency_key=key)
        self.assertEqual(self.terminals.db.execute("SELECT COUNT(*) FROM terminal_commands").fetchone()[0], 0)

    def test_uncertain_sync_action_is_not_retried(self):
        mid = self.create(actions=[{"device_id": "living_light", "capability": "toggle"}])
        original = self.runtime.call
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise Crash()
        with patch.object(self.runtime, "call", side_effect=interrupted):
            with self.assertRaises(Crash):
                self.tick()
        self.assertTrue(self.runtime.simulator.get_device("living_light")["state"]["on"])
        self.restart()
        self.tick(61)
        self.assertEqual(self.service.get(mid)["status"], "failed")
        self.assertIn("unknown", self.service.get(mid)["error"])
        self.assertTrue(self.runtime.simulator.get_device("living_light")["state"]["on"])

    def test_competing_worker_and_stale_lease_cannot_commit(self):
        mid = self.create()
        stale_job = self.service.worker._claim()
        other = MissionService(self.runtime, store=MissionStore(self.db))
        other.worker.clock = lambda: self.now
        self.addCleanup(other.close)
        other.worker.tick()
        self.assertEqual(self.terminals.db.execute("SELECT COUNT(*) FROM terminal_commands").fetchone()[0], 0)
        self.now += 61
        other.worker.tick()
        self.assertFalse(self.service.worker._save(stale_job, {"phase": "bad"}))
        self.assertEqual(self.service.get(mid)["result"]["phase"], "waiting")
        progress = self.service.get(mid)["result"]
        self.service.worker._fail(stale_job, progress, "stale worker failure")
        self.assertEqual(self.service.get(mid)["status"], "running")
        self.assertEqual(self.terminals.command("phone", progress["command_id"])["status"], "queued")

    def test_repeat_run_is_idempotent_and_schedule_is_validated(self):
        mid = self.create()
        self.service.run(mid)
        self.assertEqual(self.service.store._conn.execute("SELECT COUNT(*) FROM mission_jobs").fetchone()[0], 1)
        for value in (True, -1, float("inf"), "10"):
            with self.assertRaises(MissionError):
                self.create(delay_s=value)

    def test_worker_thread_advances_without_status_polling(self):
        mid = self.create(actions=[{"device_id": "living_light", "capability": "turn_on"}])
        self.service.worker.clock = time.time
        self.service.worker.interval = 0.01
        # The test job was created on a fake clock; move its deadline to real time.
        with self.service.store._conn:
            self.service.store._conn.execute("UPDATE mission_jobs SET deadline=? WHERE mission_id=?", (time.time()+10, mid))
        self.service.start()
        deadline = time.monotonic() + 3
        while self.service.store.get(mid)["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.service.get(mid)["status"], "completed")

    def test_slow_adapter_does_not_block_enqueue_or_cancel(self):
        mid = self.create(actions=[{"device_id": "living_light", "capability": "turn_on"}])
        second = self.service.create("next", [{"device_id": "living_light", "capability": "turn_off"}], requires_approval=False)
        entered, release = threading.Event(), threading.Event()
        original = self.runtime.call
        def slow(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)
        with patch.object(self.runtime, "call", side_effect=slow):
            worker = threading.Thread(target=self.service.worker.tick)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                start = time.monotonic()
                self.service.run(second["mission_id"])
                self.service.cancel(mid)
                self.assertLess(time.monotonic() - start, 0.5)
            finally:
                release.set()
                worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.service.get(mid)["status"], "cancelled")

    def test_legacy_running_mission_is_not_replayed(self):
        mission = self.service.create("legacy", [{"device_id": "living_light", "capability": "toggle"}], requires_approval=False)
        self.service.store.update(mission["mission_id"], status="running")
        self.service.start()
        self.assertEqual(self.service.get(mission["mission_id"])["status"], "failed")
        self.assertFalse(self.runtime.simulator.get_device("living_light")["state"]["on"])
