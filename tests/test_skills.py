import copy
import json
import unittest

from southbound.paths import SKILLS_DIR
from southbound.runtime import SouthboundRuntime
from southbound.skills import (
    SkillRegistry,
    SkillRunner,
    SkillValidationError,
)


def load_manifest(name: str) -> dict:
    return json.loads((SKILLS_DIR / f"{name}.skill.json").read_text(encoding="utf-8"))


class SkillRegistryTest(unittest.TestCase):
    def test_bundled_manifests_load(self) -> None:
        registry = SkillRegistry.from_directory(SKILLS_DIR)
        self.assertEqual(len(registry.ids()), 6)
        self.assertIn("presence_lighting", registry.ids())
        self.assertEqual(registry.get("presence_lighting")["action_tier"], "B")

    def test_manifest_rejects_forbidden_action_keys(self) -> None:
        manifest = load_manifest("presence_lighting")
        manifest["actions"][0]["script"] = "rm -rf /"
        with self.assertRaises(SkillValidationError):
            SkillRegistry.validate(manifest)

    def test_manifest_rejects_missing_actions(self) -> None:
        manifest = load_manifest("presence_lighting")
        manifest["actions"] = []
        with self.assertRaises(SkillValidationError):
            SkillRegistry.validate(manifest)

    def test_duplicate_ids_are_rejected(self) -> None:
        manifest = load_manifest("presence_lighting")
        with self.assertRaises(SkillValidationError):
            SkillRegistry({"one": manifest, "two": copy.deepcopy(manifest)})


class SkillRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = SouthboundRuntime()
        self.registry = SkillRegistry.from_directory(SKILLS_DIR)

    def test_explicit_run_goes_through_access_hub_and_verifies(self) -> None:
        runner = SkillRunner(self.runtime, self.registry, mode="shadow")
        result = runner.run("presence_lighting", force=True, mode="supervised")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "supervised")
        self.assertTrue(
            self.runtime.simulator.get_device("living_light")["state"]["on"]
        )
        types = [event["type"] for event in self.runtime.recent_events()]
        self.assertIn("command", types)
        self.assertIn("skill.verified", types)

    def test_shadow_evaluation_never_actuates(self) -> None:
        manifest = load_manifest("presence_lighting")
        manifest["guard"].pop("time_window", None)
        registry = SkillRegistry({"presence_lighting": manifest})
        self.runtime.simulator.act("entry_door", "open")
        runner = SkillRunner(self.runtime, registry, mode="shadow")
        self.assertEqual(runner.evaluate_once(now=0), [])
        decisions = runner.evaluate_once(now=3)
        self.assertTrue(decisions)
        self.assertTrue(decisions[0]["ok"])
        self.assertFalse(
            self.runtime.simulator.get_device("living_light")["state"]["on"]
        )
        types = [event["type"] for event in self.runtime.recent_events()]
        self.assertIn("skill.shadow", types)
        self.assertNotIn("skill.started", types)

    def test_cooldown_suppresses_repeat_shadow(self) -> None:
        manifest = load_manifest("presence_lighting")
        manifest["guard"].pop("time_window", None)
        manifest["trigger"]["for_s"] = 0
        registry = SkillRegistry({"presence_lighting": manifest})
        self.runtime.simulator.act("entry_door", "open")
        runner = SkillRunner(self.runtime, registry, mode="shadow")
        self.assertTrue(runner.evaluate_once(now=10))
        before = len(
            [e for e in self.runtime.recent_events() if e["type"] == "skill.shadow"]
        )
        runner.evaluate_once(now=11)
        after = len(
            [e for e in self.runtime.recent_events() if e["type"] == "skill.shadow"]
        )
        self.assertEqual(before, after)

    def test_human_approval_requires_confirm(self) -> None:
        manifest = load_manifest("presence_lighting")
        manifest["action_tier"] = "C"
        manifest["approval"] = "human"
        manifest["guard"].pop("time_window", None)
        registry = SkillRegistry({"presence_lighting": manifest})
        runner = SkillRunner(self.runtime, registry, mode="shadow")
        denied = runner.run("presence_lighting", force=True, mode="supervised")
        self.assertFalse(denied["ok"])
        self.assertTrue(denied["requires_confirmation"])
        allowed = runner.run(
            "presence_lighting", confirm=True, force=True, mode="supervised"
        )
        self.assertTrue(allowed["ok"])

    def test_planned_skill_is_blocked(self) -> None:
        runner = SkillRunner(self.runtime, self.registry, mode="shadow")
        result = runner.run("camera_observation", force=True)
        self.assertFalse(result["ok"])
        self.assertIn("planned", result["error"])

    def test_status_reports_modes_and_availability(self) -> None:
        runner = SkillRunner(self.runtime, self.registry, mode="shadow")
        status = runner.status()
        self.assertEqual(status["mode"], "shadow")
        self.assertFalse(status["running"])
        self.assertEqual(len(status["skills"]), 6)
        by_id = {item["id"]: item for item in status["skills"]}
        self.assertTrue(by_id["presence_lighting"]["available"])
        self.assertFalse(by_id["camera_observation"]["available"])

    def test_mcp_exposes_skill_tools(self) -> None:
        from southbound import mcp_server

        names = {tool["name"] for tool in mcp_server.TOOLS}
        self.assertTrue(
            {"iot_skills", "iot_skill_describe", "iot_skill_run", "iot_skill_status"}
            <= names
        )


if __name__ == "__main__":
    unittest.main()
