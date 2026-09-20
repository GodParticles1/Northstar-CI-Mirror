#!/usr/bin/env python3
"""Validate the reconstructed LS-P1-I1 fixed-SHA learning integration package."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = REPO_ROOT / "testdata" / "learning-integration" / "ls-p1-i1"


def load_json(path: Path) -> Any:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {path}:{key}")
            result[key] = value
        return result

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=unique_pairs)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def ref_key(ref: dict[str, Any]) -> tuple[str, str, int]:
    return (str(ref["repository"]), str(ref["id"]), int(ref["revision"]))


def merge_catalogs(catalogs: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "contract_version": "2.0.0",
        "resources": [],
        "evidence_targets": [],
        "experiment_targets": [],
        "units": [],
    }
    for catalog in catalogs:
        if catalog.get("contract_version") != "2.0.0":
            raise ValueError("consumer contract_version must be 2.0.0")
        for section in ("resources", "evidence_targets", "experiment_targets", "units"):
            merged[section].extend(catalog.get(section, []))
    for section in ("resources", "evidence_targets", "experiment_targets"):
        merged[section].sort(key=lambda item: ref_key(item))
    merged["units"].sort(
        key=lambda item: (str(item["owner_repository"]), str(item["id"]), int(item["revision"]))
    )
    return merged


def cycle_exists(graph: dict[tuple[str, str, int], set[tuple[str, str, int]]]) -> bool:
    visiting: set[tuple[str, str, int]] = set()
    visited: set[tuple[str, str, int]] = set()

    def visit(node: tuple[str, str, int]) -> bool:
        if node in visited:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for target in graph.get(node, set()):
            if visit(target):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def validate_package(
    repo_root: Path = REPO_ROOT,
    package_root: Path = DEFAULT_PACKAGE,
    *,
    run_northstar: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    manifest_path = package_root / "manifest.json"
    manifest = load_json(manifest_path)
    if manifest.get("task_id") != "LS-P1-I1":
        errors.append("MANIFEST_TASK_ID")
    if manifest.get("contract_version") != "2.0.0":
        errors.append("MANIFEST_CONTRACT_VERSION")

    northstar = manifest.get("northstar", {})
    if northstar.get("repository") != "GodParticles1/Northstar":
        errors.append("NORTHSTAR_AUTHORITY")
    if northstar.get("commit") != "54ac7b96c8b2b55a9b001502e8c4f6622bef03bd":
        errors.append("NORTHSTAR_FIXED_COMMIT")

    catalogs: list[dict[str, Any]] = []
    per_repository: dict[str, int] = {}
    expected = manifest.get("expected", {})
    expected_per_repo = int(expected.get("units_per_consumer", 0))

    for consumer in manifest.get("consumers", []):
        repository = str(consumer.get("repository", ""))
        snapshot = package_root / str(consumer.get("catalog_path", ""))
        if not snapshot.is_file():
            errors.append(f"CATALOG_MISSING:{repository}")
            continue
        data = snapshot.read_bytes()
        if hashlib.sha256(data).hexdigest() != consumer.get("catalog_sha256"):
            errors.append(f"CATALOG_SHA256_MISMATCH:{repository}")
        if git_blob_sha(data) != consumer.get("catalog_git_blob"):
            errors.append(f"CATALOG_GIT_BLOB_MISMATCH:{repository}")
        catalog = load_json(snapshot)
        catalogs.append(catalog)
        units = catalog.get("units", [])
        ids = [str(unit.get("id")) for unit in units]
        if ids != list(consumer.get("unit_ids", [])):
            errors.append(f"UNIT_SET_MISMATCH:{repository}")
        if len(units) != expected_per_repo:
            errors.append(f"UNIT_COUNT_MISMATCH:{repository}")
        per_repository[repository] = len(units)
        for unit in units:
            if unit.get("owner_repository") != repository:
                errors.append(f"OWNER_MISMATCH:{unit.get('id')}")
            if not isinstance(unit.get("revision"), int) or int(unit["revision"]) < 1:
                errors.append(f"REVISION_INVALID:{unit.get('id')}")

    for readonly in manifest.get("read_only_repositories", []):
        repository = str(readonly.get("repository", ""))
        if readonly.get("before") != readonly.get("after"):
            errors.append(f"READ_ONLY_REF_DRIFT:{repository}")

    try:
        merged = merge_catalogs(catalogs)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"MERGE_INPUT:{exc}")
        merged = {
            "contract_version": "2.0.0",
            "resources": [],
            "evidence_targets": [],
            "experiment_targets": [],
            "units": [],
        }

    resource_keys = {ref_key(item) for item in merged["resources"]}
    evidence_keys = {ref_key(item) for item in merged["evidence_targets"]}
    experiment_keys = {ref_key(item) for item in merged["experiment_targets"]}
    unit_keys = {
        (str(unit["owner_repository"]), str(unit["id"]), int(unit["revision"]))
        for unit in merged["units"]
        if isinstance(unit.get("revision"), int)
    }
    graph: dict[tuple[str, str, int], set[tuple[str, str, int]]] = {
        key: set() for key in unit_keys
    }

    for unit in merged["units"]:
        if not isinstance(unit.get("revision"), int):
            continue
        key = (str(unit["owner_repository"]), str(unit["id"]), int(unit["revision"]))
        for ref in unit.get("resources", []):
            if ref_key(ref) not in resource_keys:
                errors.append(f"UNRESOLVED_RESOURCE_REF:{unit.get('id')}:{ref.get('id')}")
        for ref in unit.get("prerequisites", []):
            target = ref_key(ref)
            if target not in unit_keys:
                errors.append(f"UNRESOLVED_LEARNING_UNIT_REF:{unit.get('id')}:{ref.get('id')}")
            else:
                graph.setdefault(key, set()).add(target)
        evidence = unit.get("evidence_target")
        if isinstance(evidence, dict) and ref_key(evidence) not in evidence_keys:
            errors.append(f"UNRESOLVED_EVIDENCE_REF:{unit.get('id')}:{evidence.get('id')}")
        for ref in unit.get("experiment_refs", []):
            if ref_key(ref) not in experiment_keys:
                errors.append(f"UNRESOLVED_EXPERIMENT_REF:{unit.get('id')}:{ref.get('id')}")

    if cycle_exists(graph):
        errors.append("PREREQUISITE_CYCLE")

    expected_total = int(expected.get("total_units", 0))
    if len(merged["units"]) != expected_total:
        errors.append("TOTAL_UNIT_COUNT_MISMATCH")

    northstar_result: dict[str, Any] = {"status": "NOT_RUN"}
    if run_northstar and not errors:
        with tempfile.TemporaryDirectory(prefix="ls-p1-i1-") as temp_dir:
            combined = Path(temp_dir) / "combined.catalog.json"
            combined.write_text(canonical_json(merged) + "\n", encoding="utf-8")
            command = [
                sys.executable,
                str(repo_root / "scripts" / "validate_learning_contracts.py"),
                "--catalog",
                str(combined),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                errors.append("NORTHSTAR_V2_VALIDATION_FAILED")
                northstar_result = {
                    "status": "FAIL",
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                }
            else:
                try:
                    parsed = json.loads(completed.stdout)
                    northstar_result = parsed if isinstance(parsed, dict) else {"status": "PASS"}
                except json.JSONDecodeError:
                    northstar_result = {"status": "PASS", "stdout": completed.stdout.strip()}

    checks = {
        "cross_repository_reference_resolution": "PASS" if not any("UNRESOLVED_" in e for e in errors) else "FAIL",
        "ownership": "PASS" if not any("OWNER_MISMATCH" in e for e in errors) else "FAIL",
        "revision": "PASS" if not any("REVISION_INVALID" in e for e in errors) else "FAIL",
        "prerequisite_dag": "PASS" if "PREREQUISITE_CYCLE" not in errors else "FAIL",
    }
    result = {
        "task_id": "LS-P1-I1",
        "status": "PASS" if not errors else "FAIL",
        "unit_count": len(merged["units"]),
        "per_repository": dict(sorted(per_repository.items())),
        "checks": checks,
        "read_only_repositories": {
            item["repository"]: "PASS" if item.get("before") == item.get("after") else "FAIL"
            for item in manifest.get("read_only_repositories", [])
        },
        "northstar_result": northstar_result,
        "error_codes": sorted(set(errors)),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--no-northstar", action="store_true")
    args = parser.parse_args()
    try:
        result = validate_package(REPO_ROOT, args.package, run_northstar=not args.no_northstar)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(canonical_json({"task_id": "LS-P1-I1", "status": "INPUT_ERROR", "error": str(exc)}))
        return 2
    print(canonical_json(result))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
