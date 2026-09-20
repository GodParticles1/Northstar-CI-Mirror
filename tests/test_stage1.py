from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from personal_tech_os.snapshot import build_snapshot
from personal_tech_os.stage1 import (  # noqa: E402
    Stage1ValidationError,
    can_transition,
    dumps_canonical,
    load_bundle,
    validate_bundle,
    validate_skill_graph,
)


class Stage1ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data_dir = ROOT / "data" / "stage1"
        cls.bundle = load_bundle(cls.data_dir)

    def test_current_seed_passes_all_contracts(self) -> None:
        result = validate_bundle(self.bundle)
        self.assertEqual(result.counts["plan_units"], 28)
        self.assertEqual(result.counts["unit_support"], 28)
        self.assertEqual(result.counts["tasks"], 28)
        self.assertIn("skill_dag_acyclic", result.checks)

    def test_skill_graph_rejects_cycle(self) -> None:
        skills = deepcopy(self.bundle["skills"]["items"])
        edges = deepcopy(self.bundle["skill_edges"]["items"])
        edges.append({"id": "edge.test.cycle", "prerequisite_id": "skill.cli", "skill_id": "skill.python.baseline"})
        with self.assertRaisesRegex(Stage1ValidationError, "cycle"):
            validate_skill_graph(skills, edges)

    def test_plan_rejects_missing_required_contract_field(self) -> None:
        broken = deepcopy(self.bundle)
        del broken["plan_units"]["items"][0]["remediation"]
        with self.assertRaisesRegex(Stage1ValidationError, "remediation"):
            validate_bundle(broken)

    def test_every_unit_has_support_and_lifecycle_metadata(self) -> None:
        supports = self.bundle["unit_support"]["items"]
        self.assertEqual({item["plan_unit_id"] for item in supports}, {f"D{n:02d}" for n in range(1, 29)})
        for item in supports:
            self.assertIn("youtube", item)
            self.assertIn("chinese_supplement", item)
            self.assertIn("github_references", item)
            self.assertIn("practice_mapping", item)
            self.assertIn("review_policy", item)
            self.assertIn("expiry_policy", item)

    def test_gate_units_keep_the_required_a0_policy(self) -> None:
        units = {item["id"]: item for item in self.bundle["plan_units"]["items"]}
        for unit_id in ("D01", "D07", "D14", "D21", "D28"):
            self.assertEqual(units[unit_id]["ai_policy"], "A0")

    def test_unit_support_rejects_non_https_reference(self) -> None:
        broken = deepcopy(self.bundle)
        broken["unit_support"]["items"][1]["github_references"]["items"] = ["http://example.invalid"]
        with self.assertRaisesRegex(Stage1ValidationError, "non-HTTPS"):
            validate_bundle(broken)

    def test_dangling_resource_reference_is_rejected(self) -> None:
        broken = deepcopy(self.bundle)
        broken["plan_units"]["items"][0]["official_resources"].append("resource.missing")
        with self.assertRaisesRegex(Stage1ValidationError, "unknown resources"):
            validate_bundle(broken)

    def test_state_machines_allow_only_explicit_transitions(self) -> None:
        self.assertTrue(can_transition("task", "submitted", "machine_checked"))
        self.assertTrue(can_transition("task", "failed", "remediation"))
        self.assertFalse(can_transition("task", "planned", "passed"))
        self.assertTrue(can_transition("gate", "failed", "remediation"))
        self.assertFalse(can_transition("gate", "locked", "passed"))

    def test_snapshot_is_deterministic_under_input_order_changes(self) -> None:
        reordered = deepcopy(self.bundle)
        reordered["resources"]["items"].reverse()
        reordered["plan_units"]["items"].reverse()
        self.assertEqual(dumps_canonical(self.bundle), dumps_canonical(reordered))

    def test_committed_snapshot_matches_seed(self) -> None:
        expected = (ROOT / "snapshots" / "stage1-v1.json").read_text(encoding="utf-8")
        self.assertEqual(expected, build_snapshot(ROOT))

    def test_canonical_snapshot_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(dumps_canonical(self.bundle), encoding="utf-8")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "1.0.0")

    def test_all_json_schemas_are_valid_json_and_identified(self) -> None:
        schemas = sorted((ROOT / "schemas" / "v1").glob("*.json"))
        self.assertGreaterEqual(len(schemas), 4)
        for path in schemas:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertTrue(payload["$id"].endswith(path.name))


if __name__ == "__main__":
    unittest.main()
