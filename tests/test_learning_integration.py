from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_learning_integration.py"
PACKAGE = ROOT / "testdata" / "learning-integration" / "ls-p1-i1"

spec = importlib.util.spec_from_file_location("validate_learning_integration", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_consumer_hashes(manifest: dict[str, Any], package: Path, repository: str) -> None:
    consumer = next(item for item in manifest["consumers"] if item["repository"] == repository)
    path = package / consumer["catalog_path"]
    data = path.read_bytes()
    consumer["catalog_sha256"] = hashlib.sha256(data).hexdigest()
    consumer["catalog_git_blob"] = module.git_blob_sha(data)


class IntegrationValidationTests(unittest.TestCase):
    def copied_package(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory(prefix="ls-p1-i1-test-")
        target = Path(temp.name) / "package"
        shutil.copytree(PACKAGE, target)
        return temp, target

    def test_fixed_package_passes_semantic_checks(self) -> None:
        result = module.validate_package(ROOT, PACKAGE, run_northstar=False)
        self.assertEqual("PASS", result["status"], result)
        self.assertEqual(6, result["unit_count"])
        self.assertEqual(
            {
                "GodParticles1/Cortex": 2,
                "GodParticles1/Forge": 2,
                "GodParticles1/Nimbus": 2,
            },
            result["per_repository"],
        )

    def test_catalog_tampering_is_rejected(self) -> None:
        temp, package = self.copied_package()
        self.addCleanup(temp.cleanup)
        path = package / "catalogs" / "forge.catalog.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        result = module.validate_package(ROOT, package, run_northstar=False)
        self.assertEqual("FAIL", result["status"])
        self.assertTrue(any(code.startswith("CATALOG_SHA256_MISMATCH") for code in result["error_codes"]))

    def test_owner_mismatch_is_rejected_even_with_relocked_snapshot(self) -> None:
        temp, package = self.copied_package()
        self.addCleanup(temp.cleanup)
        manifest_path = package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        catalog_path = package / "catalogs" / "forge.catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["units"][0]["owner_repository"] = "GodParticles1/Cortex"
        write_json(catalog_path, catalog)
        update_consumer_hashes(manifest, package, "GodParticles1/Forge")
        write_json(manifest_path, manifest)
        result = module.validate_package(ROOT, package, run_northstar=False)
        self.assertEqual("FAIL", result["status"])
        self.assertIn("OWNER_MISMATCH:forge.unit.deterministic-python-contracts", result["error_codes"])

    def test_read_only_reference_drift_is_rejected(self) -> None:
        temp, package = self.copied_package()
        self.addCleanup(temp.cleanup)
        manifest_path = package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        changed = copy.deepcopy(manifest)
        changed["read_only_repositories"][0]["after"]["tree"] = "0" * 40
        write_json(manifest_path, changed)
        result = module.validate_package(ROOT, package, run_northstar=False)
        self.assertEqual("FAIL", result["status"])
        self.assertIn("READ_ONLY_REF_DRIFT:GodParticles1/Proof", result["error_codes"])


if __name__ == "__main__":
    unittest.main()
