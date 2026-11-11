"""Persistent, leased, sequential mission execution.

Each step is checkpointed BEFORE dispatch. Terminal steps have a stable key
across both databases, closing the enqueue/checkpoint crash window. Synchronous
actions with an uncertain outcome are never automatically repeated.
"""
from __future__ import annotations

import json
import math
import threading
import time
import uuid

from .access.adapters.endpoint import EndpointAdapter
from .missions import MissionError, _matches, _now
from .terminals import command_id_for


class MissionWorker:
    def __init__(self, service, clock=time.time, interval=0.5):
        self.service = service
        self.store = service.store
        self.runtime = service.runtime
        self.clock = clock
        self.interval = interval
        self.owner = uuid.uuid4().hex
        self.lease_s = 60
        self._stop = threading.Event()
        self._thread = None
        self.last_error = ""

    def start(self):
        if self._thread is not None:
            return
        # Pre-upgrade synchronous jobs have no durable action cursor.
        with self.store._lock, self.store._conn:
            self.store._conn.execute(
                "UPDATE missions SET status='failed',error=?,updated_at=? WHERE status='running' "
                "AND mission_id NOT IN (SELECT mission_id FROM mission_jobs)",
                ("Legacy running job has an unknown outcome; it was not repeated", _now()),
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mission-worker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise RuntimeError("mission worker is still executing; store must remain open")
            self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = ""
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.runtime.events.publish({"type": "mission.worker.error", "ok": False,
                                             "error": self.last_error})
            self._stop.wait(self.interval)

    def enqueue(self, mission):
        schedule = mission["metadata"].get("schedule") or {}
        if not isinstance(schedule, dict):
            raise MissionError("invalid persisted schedule")
        for field, default, lower, upper in (("delay_s", 0, 0, 2592000), ("timeout_s", 600, 10, 86400)):
            value = schedule.get(field, default)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not lower <= value <= upper):
                raise MissionError(f"invalid persisted {field}")
        now = self.clock()
        run_after = now + float(schedule.get("delay_s", 0))
        deadline = run_after + float(schedule.get("timeout_s", 600))
        progress = {"phase": "ready", "next_action": 0, "actions": [],
                    "scheduled_at": run_after, "deadline": deadline}
        with self.store._lock, self.store._conn:
            updated = self.store._conn.execute(
                "UPDATE missions SET status='running',result=?,updated_at=? "
                "WHERE mission_id=? AND status='approved'",
                (json.dumps(progress), _now(), mission["mission_id"]),
            )
            if updated.rowcount == 1:
                self.store._conn.execute("INSERT INTO mission_jobs "
                                         "(mission_id,progress,run_after,deadline) VALUES (?,?,?,?)",
                                         (mission["mission_id"], json.dumps(progress), run_after, deadline))
        result = self.service.get(mission["mission_id"])
        self.service._emit("mission.scheduled", result)
        return result

    def _claim(self):
        now = self.clock()
        with self.store._lock, self.store._conn:
            row = self.store._conn.execute(
                "SELECT j.* FROM mission_jobs j JOIN missions m USING(mission_id) "
                "WHERE m.status='running' AND j.run_after<=? AND j.lease_until<=? "
                "ORDER BY j.run_after,j.mission_id LIMIT 1", (now, now),
            ).fetchone()
            if row is None:
                return None
            updated = self.store._conn.execute(
                "UPDATE mission_jobs SET lease_owner=?,lease_until=? WHERE mission_id=? AND lease_until<=? "
                "AND EXISTS (SELECT 1 FROM missions WHERE mission_id=? AND status='running')",
                (self.owner, now + self.lease_s, row["mission_id"], now, row["mission_id"]),
            )
            return dict(row) if updated.rowcount == 1 else None

    def _save(self, job, progress, status="running", error="", release=True):
        now = self.clock()
        payload = json.dumps(progress, ensure_ascii=False, allow_nan=False)
        with self.store._lock, self.store._conn:
            updated = self.store._conn.execute(
                "UPDATE mission_jobs SET progress=?,run_after=?,lease_owner=?,lease_until=? "
                "WHERE mission_id=? AND lease_owner=? AND lease_until>? "
                "AND EXISTS (SELECT 1 FROM missions WHERE mission_id=? AND status='running')",
                (payload, -1 if status != "running" and not progress.get("cancellation_pending") else now + self.interval,
                 "" if release else self.owner,
                 0 if release else now + self.lease_s, job["mission_id"], self.owner,
                 now, job["mission_id"]),
            )
            if not updated.rowcount:
                return False
            self.store._conn.execute("UPDATE missions SET status=?,result=?,error=?,updated_at=? WHERE mission_id=?",
                                     (status, payload, error, _now(), job["mission_id"]))
        return True

    def tick(self):
        """One bounded scheduling pass; also exposed for deterministic tests."""
        self.cleanup_cancelled()
        for _ in range(100):
            if self._stop.is_set():
                return
            job = self._claim()
            if job is None:
                return
            progress = json.loads(job["progress"])
            mission = self.service.get(job["mission_id"])
            try:
                if self.clock() >= job["deadline"]:
                    self._fail(job, progress, "mission deadline exceeded")
                else:
                    self._advance(job, progress, mission)
            except Exception as exc:
                self._fail(job, progress, f"{type(exc).__name__}: {exc}")

    def _terminal(self):
        adapter = self.runtime.hub.adapters.get("endpoint")
        if not isinstance(adapter, EndpointAdapter):
            raise ValueError("endpoint adapter unavailable")
        return adapter

    def _advance(self, job, progress, mission):
        index = progress["next_action"]
        if index >= len(mission["actions"]):
            self._verify(job, progress, mission)
            return
        action = mission["actions"][index]
        if progress["phase"] in ("waiting", "waiting_for_user"):
            command = self._terminal().store.command(action["device_id"], progress["command_id"])
            if command["status"] in ("queued", "leased"):
                update = command['result'].get('progress', {})
                progress['phase'] = 'waiting_for_user' if update.get('phase') == 'waiting_for_user' else 'waiting'
                progress['waiting_message'] = update.get('message', '')
                self._save(job, progress)
                return
            if command["status"] != "succeeded":
                self._fail(job, progress, f"terminal command {command['status']}: {command['result']}")
                return
            self._complete_step(job, progress, mission, {"device_id": action["device_id"],
                                                       "capability": action["capability"], "command": command})
            return

        binding = self.runtime.hub.describe(action["device_id"])
        capability = next((c for c in binding["capabilities"] if c["name"] == action["capability"]), None)
        asynchronous = capability is not None and capability.get("returns") == "command"
        if asynchronous and binding["adapter"] != "endpoint":
            raise ValueError("async adapter has no durable receipt resolver")
        if progress["phase"] == "dispatching" and not progress.get("idempotency_key"):
            self._fail(job, progress, "synchronous action outcome unknown after interruption; not retried")
            return
        if progress["phase"] == "dispatching" and progress.get("idempotency_key") and not asynchronous:
            self._fail(job, progress, "adapter contract changed during recovery")
            return
        key = f"mission:{mission['mission_id']}:{index}" if asynchronous else ""
        progress.update(phase="dispatching", idempotency_key=key,
                        command_id=command_id_for(key) if key else "", device_id=action["device_id"])
        if not self._save(job, progress, release=False):
            return
        result = self.runtime.call(action["device_id"], action["capability"], action["arguments"],
                                   confirm=action["confirm"], source=f"mission:{mission['mission_id']}",
                                   idempotency_key=key)
        if asynchronous:
            if result["result"].get("command_id") != progress["command_id"]:
                raise ValueError("adapter returned a different command ID")
            progress["phase"] = "waiting"
            self._save(job, progress)
        else:
            self._complete_step(job, progress, mission, result)

    def _complete_step(self, job, progress, mission, result):
        progress["actions"].append(result)
        progress["next_action"] += 1
        progress["phase"] = "ready"
        for field in ("command_id", "idempotency_key", "device_id"):
            progress.pop(field, None)
        if self._save(job, progress):
            self.service._emit("mission.action", self.service.get(mission["mission_id"]), result=result)

    def _verify(self, job, progress, mission):
        checks = []
        for spec in mission["verify"]:
            device_id = spec["device_id"]
            binding = self.runtime.hub.describe(device_id)
            if binding["adapter"] == "endpoint":
                # Only receipts from THIS execution can verify terminal fields.
                state = {}
                for result in progress["actions"]:
                    if result.get("device_id") == device_id and "command" in result:
                        state.update(result["command"]["result"])
                source = "terminal_receipt"
            else:
                readback = self.runtime.call(device_id, "read", source="mission.verify")["result"]
                state = readback.get("state") if isinstance(readback.get("state"), dict) else readback
                source = "device_readback"
            value = state
            for field in spec["field"].split("."):
                value = value.get(field) if isinstance(value, dict) else None
            checks.append({"device_id": device_id, "field": spec["field"], "value": value,
                           "ok": _matches(value, spec), "source": source})
        progress["verification"] = checks
        if not all(check["ok"] for check in checks):
            progress.setdefault("verify_deadline", min(job["deadline"], self.clock() + 5))
            if self.clock() >= progress["verify_deadline"]:
                self._fail(job, progress, "state verification failed")
            else:
                progress["phase"] = "verifying"
                self._save(job, progress)
            return
        status = "verified" if checks else "completed"
        progress["phase"] = status
        if self._save(job, progress, status=status):
            self.service._emit("mission." + status, self.service.get(mission["mission_id"]), result=progress)

    def _cancel_command(self, progress):
        try:
            if progress.get("command_id") and progress.get("device_id"):
                self._terminal().store.cancel(progress["device_id"], progress["command_id"])
        except Exception as exc:
            # An unavailable adapter must not block every other mission. Keep
            # the cleanup intent durable and retry when it becomes available.
            progress["cancellation_pending"] = True
            progress["cancellation_error"] = f"{type(exc).__name__}: {exc}"
            return False
        progress.pop("cancellation_pending", None)
        progress.pop("cancellation_error", None)
        return True

    def _fail(self, job, progress, error):
        progress["phase"] = "failed"
        progress["error"] = error
        if progress.get("command_id") and progress.get("device_id"):
            progress["cancellation_pending"] = True
        # Fence the failure before touching the terminal queue. A stale worker
        # must not cancel a command now owned by another lease holder.
        if self._save(job, progress, status="failed", error=error):
            self.cleanup_cancelled()
            self.service._emit("mission.failed", self.service.get(job["mission_id"]), error=error, ok=False)

    def cleanup_cancelled(self):
        with self.store._lock:
            rows = self.store._conn.execute("SELECT j.*,m.status FROM mission_jobs j JOIN missions m USING(mission_id) "
                                            "WHERE m.status IN ('cancelled','failed') AND j.run_after>=0").fetchall()
        for row in rows:
            progress = json.loads(row["progress"])
            if row["status"] == "failed" and not progress.get("cancellation_pending"):
                continue
            cleaned = self._cancel_command(progress)
            progress["phase"] = row["status"]
            with self.store._lock, self.store._conn:
                self.store._conn.execute("UPDATE mission_jobs SET progress=?,run_after=?,lease_owner='',lease_until=0 WHERE mission_id=?",
                                         (json.dumps(progress), -1 if cleaned else self.clock(), row["mission_id"]))
                self.store._conn.execute("UPDATE missions SET result=?,updated_at=? WHERE mission_id=? AND status=?",
                                         (json.dumps(progress), _now(), row["mission_id"], row["status"]))
