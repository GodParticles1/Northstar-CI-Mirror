"""Load and validate the Stage 1 file model without third-party dependencies."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


DATA_FILES = {
    "skills": "skills.json",
    "skill_edges": "skill-edges.json",
    "stages": "stages.json",
    "gates": "gates.json",
    "resources": "resources.json",
    "plan_windows": "plan-windows.json",
    "plan_units": "plan-units.json",
    "unit_support": "unit-support.json",
    "tasks": "tasks.json",
    "evidence": "evidence.json",
}

PLAN_UNIT_REQUIRED_FIELDS = {
    "id",
    "sequence",
    "title",
    "content",
    "why",
    "prerequisites",
    "official_resources",
    "resource_scope",
    "independent_work",
    "tests",
    "git_evidence",
    "readme_evidence",
    "acceptance",
    "remediation",
    "ai_policy",
}

RESOURCE_REQUIRED_FIELDS = {
    "id",
    "title",
    "authority",
    "language",
    "difficulty",
    "access",
    "status",
    "checked_at",
    "locators",
    "scope",
    "alternative",
}

STATE_TRANSITIONS = {
    "editorial": {
        "candidate": {"active", "rejected"},
        "active": {"deprecated"},
        "deprecated": set(),
        "rejected": set(),
    },
    "task": {
        "planned": {"ready"},
        "ready": {"in_progress"},
        "in_progress": {"submitted", "failed"},
        "submitted": {"machine_checked", "failed"},
        "machine_checked": {"passed", "failed"},
        "failed": {"remediation"},
        "remediation": {"ready"},
        "passed": set(),
    },
    "gate": {
        "locked": {"eligible"},
        "eligible": {"attempted"},
        "attempted": {"passed", "failed"},
        "failed": {"remediation"},
        "remediation": {"eligible"},
        "passed": set(),
    },
}


class Stage1ValidationError(ValueError):
    """Raised when Stage 1 data violates a deterministic contract."""


@dataclass(frozen=True)
class ValidationResult:
    checks: tuple[str, ...]
    counts: Mapping[str, int]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Stage1ValidationError(f"missing data file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Stage1ValidationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise Stage1ValidationError(f"{path} must contain an object with an items array")
    return payload


def load_bundle(data_dir: Path) -> dict[str, dict[str, Any]]:
    """Load every Stage 1 data file from *data_dir*."""

    return {name: _load_json(data_dir / filename) for name, filename in DATA_FILES.items()}


def _require_unique_ids(items: Iterable[Mapping[str, Any]], kind: str) -> set[str]:
    seen: set[str] = set()
    for index, item in enumerate(items):
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise Stage1ValidationError(f"{kind}[{index}] has no non-empty string id")
        if item_id in seen:
            raise Stage1ValidationError(f"duplicate {kind} id: {item_id}")
        seen.add(item_id)
    return seen


def validate_skill_graph(skills: list[Mapping[str, Any]], edges: list[Mapping[str, Any]]) -> None:
    skill_ids = _require_unique_ids(skills, "skill")
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {skill_id: 0 for skill_id in skill_ids}

    for edge in edges:
        source = edge.get("prerequisite_id")
        target = edge.get("skill_id")
        if source not in skill_ids or target not in skill_ids:
            raise Stage1ValidationError(f"skill edge has dangling reference: {source!r} -> {target!r}")
        if source == target:
            raise Stage1ValidationError(f"skill graph self-cycle: {source}")
        adjacency[source].append(target)
        indegree[target] += 1

    queue = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for child in sorted(adjacency[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(skill_ids):
        cyclic = sorted(node for node, degree in indegree.items() if degree > 0)
        raise Stage1ValidationError(f"skill graph contains a cycle involving: {', '.join(cyclic)}")


def validate_resources(resources: list[Mapping[str, Any]]) -> None:
    _require_unique_ids(resources, "resource")
    for resource in resources:
        missing = sorted(field for field in RESOURCE_REQUIRED_FIELDS if field not in resource)
        if missing:
            raise Stage1ValidationError(f"resource {resource.get('id')} missing fields: {', '.join(missing)}")
        locators = resource["locators"]
        if not isinstance(locators, list) or not locators:
            raise Stage1ValidationError(f"resource {resource['id']} needs at least one locator")
        for locator in locators:
            if locator.get("kind") != "url" or not str(locator.get("value", "")).startswith("https://"):
                raise Stage1ValidationError(f"resource {resource['id']} has a non-HTTPS or invalid locator")


def validate_plan_units(
    units: list[Mapping[str, Any]],
    resource_ids: set[str],
    skill_ids: set[str],
) -> None:
    unit_ids = _require_unique_ids(units, "plan unit")
    if len(units) != 28:
        raise Stage1ValidationError(f"month01 must contain exactly 28 units, found {len(units)}")
    sequences = sorted(unit.get("sequence") for unit in units)
    if sequences != list(range(1, 29)):
        raise Stage1ValidationError("plan unit sequences must be the integers 1 through 28")

    for unit in units:
        missing = sorted(PLAN_UNIT_REQUIRED_FIELDS - set(unit))
        if missing:
            raise Stage1ValidationError(f"plan unit {unit.get('id')} missing fields: {', '.join(missing)}")
        unknown_resources = sorted(set(unit["official_resources"]) - resource_ids)
        if unknown_resources:
            raise Stage1ValidationError(
                f"plan unit {unit['id']} references unknown resources: {', '.join(unknown_resources)}"
            )
        unknown_skills = sorted(set(unit.get("skill_ids", [])) - skill_ids)
        if unknown_skills:
            raise Stage1ValidationError(
                f"plan unit {unit['id']} references unknown skills: {', '.join(unknown_skills)}"
            )
        unknown_units = sorted(
            prerequisite for prerequisite in unit["prerequisites"] if prerequisite.startswith("D") and prerequisite not in unit_ids
        )
        if unknown_units:
            raise Stage1ValidationError(
                f"plan unit {unit['id']} references unknown units: {', '.join(unknown_units)}"
            )
        if unit["ai_policy"] not in {"A0", "A1"}:
            raise Stage1ValidationError(f"plan unit {unit['id']} has invalid ai_policy")


def can_transition(machine: str, current: str, target: str) -> bool:
    """Return whether a state transition is explicitly allowed."""

    try:
        return target in STATE_TRANSITIONS[machine][current]
    except KeyError as exc:
        raise Stage1ValidationError(f"unknown state machine or state: {machine}/{current}") from exc


def validate_bundle(bundle: Mapping[str, Mapping[str, Any]]) -> ValidationResult:
    """Validate cross-file Stage 1 invariants and return named checks."""

    missing_files = sorted(set(DATA_FILES) - set(bundle))
    if missing_files:
        raise Stage1ValidationError(f"bundle missing sections: {', '.join(missing_files)}")

    skills = bundle["skills"]["items"]
    edges = bundle["skill_edges"]["items"]
    resources = bundle["resources"]["items"]
    units = bundle["plan_units"]["items"]
    unit_support = bundle["unit_support"]["items"]
    stages = bundle["stages"]["items"]
    gates = bundle["gates"]["items"]
    windows = bundle["plan_windows"]["items"]
    tasks = bundle["tasks"]["items"]
    evidence = bundle["evidence"]["items"]

    skill_ids = _require_unique_ids(skills, "skill")
    validate_skill_graph(skills, edges)
    validate_resources(resources)
    resource_ids = {item["id"] for item in resources}
    validate_plan_units(units, resource_ids, skill_ids)
    _require_unique_ids(unit_support, "unit support")
    stage_ids = _require_unique_ids(stages, "stage")
    gate_ids = _require_unique_ids(gates, "gate")
    window_ids = _require_unique_ids(windows, "plan window")
    task_ids = _require_unique_ids(tasks, "task")
    _require_unique_ids(evidence, "evidence")

    for gate in gates:
        if gate.get("stage_id") not in stage_ids:
            raise Stage1ValidationError(f"gate {gate['id']} references unknown stage")
    unit_ids = {item["id"] for item in units}
    supported_unit_ids = [item.get("plan_unit_id") for item in unit_support]
    if len(supported_unit_ids) != len(set(supported_unit_ids)):
        raise Stage1ValidationError("unit support contains duplicate plan_unit_id values")
    if set(supported_unit_ids) != unit_ids:
        missing = sorted(unit_ids - set(supported_unit_ids))
        extra = sorted(set(supported_unit_ids) - unit_ids)
        raise Stage1ValidationError(f"unit support coverage mismatch; missing={missing}, extra={extra}")
    for support in unit_support:
        for field in ("youtube", "chinese_supplement", "github_references", "practice_mapping", "review_policy", "expiry_policy"):
            if field not in support:
                raise Stage1ValidationError(f"unit support {support['id']} missing field: {field}")
        for field in ("youtube", "chinese_supplement", "github_references"):
            selection = support[field]
            if not isinstance(selection, dict) or not isinstance(selection.get("status"), str):
                raise Stage1ValidationError(f"unit support {support['id']} has invalid {field} status")
            if not isinstance(selection.get("items"), list):
                raise Stage1ValidationError(f"unit support {support['id']} has invalid {field} items")
            for locator in selection["items"]:
                if not isinstance(locator, str) or not locator.startswith("https://"):
                    raise Stage1ValidationError(f"unit support {support['id']} has a non-HTTPS {field} locator")
        mapping = support["practice_mapping"]
        if not isinstance(mapping, dict) or not all(
            isinstance(mapping.get(key), str) and mapping[key].strip()
            for key in ("sre_platform", "tech_radar")
        ):
            raise Stage1ValidationError(f"unit support {support['id']} has an incomplete practice mapping")
        schedule = support["review_policy"].get("schedule")
        if not isinstance(schedule, list) or not schedule:
            raise Stage1ValidationError(f"unit support {support['id']} has no review schedule")
    for task in tasks:
        if task.get("plan_unit_id") not in unit_ids:
            raise Stage1ValidationError(f"task {task['id']} references unknown plan unit")
        if task.get("plan_window_id") not in window_ids:
            raise Stage1ValidationError(f"task {task['id']} references unknown plan window")
        if task.get("state") not in STATE_TRANSITIONS["task"]:
            raise Stage1ValidationError(f"task {task['id']} has unknown state")
    for gate in gates:
        if gate.get("state") not in STATE_TRANSITIONS["gate"]:
            raise Stage1ValidationError(f"gate {gate['id']} has unknown state")

    return ValidationResult(
        checks=(
            "all_data_files_loaded",
            "ids_unique",
            "skill_dag_acyclic",
            "resource_contract_complete",
            "plan_contains_28_structured_units",
            "unit_support_contract_complete",
            "cross_references_resolved",
            "state_values_known",
        ),
        counts={
            "skills": len(skills),
            "skill_edges": len(edges),
            "stages": len(stages),
            "gates": len(gates),
            "resources": len(resources),
            "plan_windows": len(windows),
            "plan_units": len(units),
            "unit_support": len(unit_support),
            "tasks": len(tasks),
            "evidence": len(evidence),
        },
    )


def canonical_bundle(bundle: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Return a deep-copied, stable ordering suitable for a Git snapshot."""

    stable: dict[str, Any] = {"schema_version": "1.0.0", "sections": {}}
    for section in sorted(DATA_FILES):
        items = deepcopy(bundle[section]["items"])
        items.sort(key=lambda item: (item.get("sequence", 0), item.get("id", "")))
        stable["sections"][section] = items
    return stable


def dumps_canonical(bundle: Mapping[str, Mapping[str, Any]]) -> str:
    return json.dumps(canonical_bundle(bundle), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
