# pyright: strict, reportMissingModuleSource=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

"""Validate Learning System v2 schemas, catalogs, references, and dependency graphs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

JSONValue: TypeAlias = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)
JSONObject: TypeAlias = dict[str, JSONValue]
ReferenceKey: TypeAlias = tuple[str, str, str, int]
UnitKey: TypeAlias = tuple[str, str, int]

SCHEMA_VERSION = "2.0.0"
OWNER_PREFIXES = {
    "GodParticles1/Forge": "forge",
    "GodParticles1/Cortex": "cortex",
    "GodParticles1/Nimbus": "nimbus",
    "GodParticles1/Proof": "proof",
    "GodParticles1/Catalyst": "catalyst",
}


class ContractLoadError(RuntimeError):
    """Raised when a required JSON document cannot be loaded."""


class SuiteConfigurationError(RuntimeError):
    """Raised when a fixture-suite manifest is malformed."""


@dataclass(frozen=True, order=True)
class ContractError:
    """One deterministic validation failure."""

    code: str
    location: str
    message: str

    def as_json(self) -> dict[str, str]:
        return {"code": self.code, "location": self.location, "message": self.message}


@dataclass(frozen=True)
class ValidationResult:
    """A stable result for one catalog."""

    errors: tuple[ContractError, ...]
    unit_count: int

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(sorted({error.code for error in self.errors}))

    def as_json(self) -> dict[str, JSONValue]:
        return cast(
            dict[str, JSONValue],
            {
                "status": "PASS" if self.is_valid else "FAIL",
                "unit_count": self.unit_count,
                "error_codes": list(self.error_codes),
                "errors": [error.as_json() for error in self.errors],
            },
        )


def load_json(path: Path) -> JSONValue:
    """Load one JSON document without accepting non-JSON extensions or fallbacks."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractLoadError(f"cannot read {path}: {exc}") from exc

    try:
        return cast(JSONValue, json.loads(text))
    except json.JSONDecodeError as exc:
        raise ContractLoadError(
            f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def require_object(value: JSONValue, *, context: str) -> JSONObject:
    if not isinstance(value, dict):
        raise ContractLoadError(f"{context} must be a JSON object")
    return value


def require_string(value: JSONValue, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractLoadError(f"{context} must be a non-empty string")
    return value


def json_path(base: str, parts: list[object]) -> str:
    result = base
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}"
    return result


def validation_error_key(error: ValidationError) -> tuple[str, str]:
    path = json_path("$", list(cast(list[object], error.absolute_path)))
    return path, error.message


def reference_key(value: JSONObject) -> ReferenceKey | None:
    kind = value.get("kind")
    repository = value.get("repository")
    identifier = value.get("id")
    revision = value.get("revision")
    if (
        isinstance(kind, str)
        and isinstance(repository, str)
        and isinstance(identifier, str)
        and isinstance(revision, int)
        and not isinstance(revision, bool)
    ):
        return kind, repository, identifier, revision
    return None


def unit_key(value: JSONObject) -> UnitKey | None:
    repository = value.get("owner_repository")
    identifier = value.get("id")
    revision = value.get("revision")
    if (
        isinstance(repository, str)
        and isinstance(identifier, str)
        and isinstance(revision, int)
        and not isinstance(revision, bool)
    ):
        return repository, identifier, revision
    return None


def object_items(value: JSONValue) -> list[JSONObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


class LearningContractValidator:
    """Draft 2020-12 plus deterministic cross-document semantic validation."""

    def __init__(self, refs_schema: JSONObject, unit_schema: JSONObject) -> None:
        Draft202012Validator.check_schema(refs_schema)
        Draft202012Validator.check_schema(unit_schema)

        refs_id = require_string(refs_schema.get("$id"), context="reference schema $id")
        refs_resource = Resource.from_contents(refs_schema)
        registry = Registry().with_resource(refs_id, refs_resource)

        self._refs_validator = Draft202012Validator(
            refs_schema,
            format_checker=FormatChecker(),
        )
        self._unit_validator = Draft202012Validator(
            unit_schema,
            registry=registry,
            format_checker=FormatChecker(),
        )

    @classmethod
    def from_schema_dir(cls, schema_dir: Path) -> LearningContractValidator:
        refs = require_object(
            load_json(schema_dir / "learning-refs.schema.json"),
            context="reference schema",
        )
        units = require_object(
            load_json(schema_dir / "learning-unit.schema.json"),
            context="unit schema",
        )
        return cls(refs, units)

    def validate_path(self, catalog_path: Path) -> ValidationResult:
        return self.validate(load_json(catalog_path))

    def _schema_errors(
        self,
        validator: Draft202012Validator,
        value: JSONValue,
        *,
        base: str,
        map_missing_evidence: bool = False,
    ) -> list[ContractError]:
        errors: list[ContractError] = []
        for error in sorted(validator.iter_errors(value), key=validation_error_key):
            if (
                map_missing_evidence
                and error.validator == "required"
                and isinstance(value, dict)
                and "evidence_target" not in value
                and isinstance(error.validator_value, list)
                and "evidence_target" in error.validator_value
            ):
                errors.append(
                    ContractError(
                        "MISSING_EVIDENCE_TARGET",
                        f"{base}.evidence_target",
                        "Knowledge Unit must declare one EvidenceRef target",
                    )
                )
                continue

            absolute = list(cast(list[object], error.absolute_path))
            errors.append(
                ContractError(
                    "SCHEMA_INVALID",
                    json_path(base, absolute),
                    error.message,
                )
            )
        return errors

    @staticmethod
    def _owner_errors(value: JSONObject, *, location: str) -> list[ContractError]:
        errors: list[ContractError] = []
        key = reference_key(value)
        if key is None:
            return errors

        kind, repository, identifier, _revision = key
        owner_prefix = OWNER_PREFIXES.get(repository)
        segment_by_kind = {
            "LearningUnitRef": "unit",
            "ResourceRef": "resource",
            "PracticeRef": "practice",
            "EvidenceRef": "evidence",
            "ExperimentRef": "experiment",
        }
        segment = segment_by_kind.get(kind)
        if owner_prefix is None or segment is None:
            return errors

        expected_prefix = f"{owner_prefix}.{segment}."
        if not identifier.startswith(expected_prefix):
            errors.append(
                ContractError(
                    "OWNER_VIOLATION",
                    location,
                    f"{identifier!r} is not owned by {repository!r} as {kind}",
                )
            )
        return errors

    @staticmethod
    def _unit_owner_errors(unit: JSONObject, *, location: str) -> list[ContractError]:
        repository = unit.get("owner_repository")
        identifier = unit.get("id")
        if not isinstance(repository, str) or not isinstance(identifier, str):
            return []
        owner_prefix = OWNER_PREFIXES.get(repository)
        if owner_prefix is None:
            return []
        if identifier.startswith(f"{owner_prefix}.unit."):
            return []
        return [
            ContractError(
                "OWNER_VIOLATION",
                f"{location}.id",
                f"{identifier!r} is not owned by {repository!r}",
            )
        ]

    @staticmethod
    def _cycles(graph: dict[UnitKey, tuple[UnitKey, ...]]) -> tuple[tuple[UnitKey, ...], ...]:
        state: dict[UnitKey, int] = {}
        stack: list[UnitKey] = []
        stack_indexes: dict[UnitKey, int] = {}
        cycles: set[tuple[UnitKey, ...]] = set()

        def visit(node: UnitKey) -> None:
            state[node] = 1
            stack_indexes[node] = len(stack)
            stack.append(node)

            for dependency in sorted(graph.get(node, ())):
                dependency_state = state.get(dependency, 0)
                if dependency_state == 0:
                    visit(dependency)
                elif dependency_state == 1:
                    start = stack_indexes[dependency]
                    cycles.add(tuple(sorted(set(stack[start:]))))

            stack.pop()
            stack_indexes.pop(node, None)
            state[node] = 2

        for node in sorted(graph):
            if state.get(node, 0) == 0:
                visit(node)

        return tuple(sorted(cycles))

    def validate(self, catalog_value: JSONValue) -> ValidationResult:
        errors: list[ContractError] = []
        if not isinstance(catalog_value, dict):
            return ValidationResult(
                errors=(
                    ContractError(
                        "CATALOG_INVALID",
                        "$",
                        "catalog must be a JSON object",
                    ),
                ),
                unit_count=0,
            )

        version = catalog_value.get("contract_version")
        if version != SCHEMA_VERSION:
            errors.append(
                ContractError(
                    "CATALOG_INVALID",
                    "$.contract_version",
                    f"expected contract_version {SCHEMA_VERSION!r}",
                )
            )

        list_names = ("resources", "evidence_targets", "experiment_targets", "units")
        lists: dict[str, list[JSONValue]] = {}
        for name in list_names:
            value = catalog_value.get(name)
            if not isinstance(value, list):
                errors.append(
                    ContractError(
                        "CATALOG_INVALID",
                        f"$.{name}",
                        f"{name} must be an array",
                    )
                )
                lists[name] = []
            else:
                lists[name] = value

        expected_kinds = {
            "resources": "ResourceRef",
            "evidence_targets": "EvidenceRef",
            "experiment_targets": "ExperimentRef",
        }
        declared: dict[str, set[ReferenceKey]] = {
            name: set() for name in expected_kinds
        }

        for name, expected_kind in expected_kinds.items():
            for index, item in enumerate(lists[name]):
                location = f"$.{name}[{index}]"
                errors.extend(
                    self._schema_errors(self._refs_validator, item, base=location)
                )
                if not isinstance(item, dict):
                    continue
                key = reference_key(item)
                if key is None:
                    continue
                if key[0] != expected_kind:
                    errors.append(
                        ContractError(
                            "REFERENCE_KIND_MISMATCH",
                            f"{location}.kind",
                            f"expected {expected_kind}, got {key[0]}",
                        )
                    )
                    continue
                errors.extend(self._owner_errors(item, location=location))
                declared[name].add(key)

        unit_objects: list[tuple[int, JSONObject]] = []
        units_by_key: dict[UnitKey, JSONObject] = {}
        unit_indexes: dict[UnitKey, int] = {}
        seen_ids: dict[str, int] = {}

        for index, item in enumerate(lists["units"]):
            location = f"$.units[{index}]"
            errors.extend(
                self._schema_errors(
                    self._unit_validator,
                    item,
                    base=location,
                    map_missing_evidence=True,
                )
            )
            if not isinstance(item, dict):
                continue
            unit_objects.append((index, item))
            errors.extend(self._unit_owner_errors(item, location=location))

            identifier = item.get("id")
            if isinstance(identifier, str):
                first_index = seen_ids.get(identifier)
                if first_index is not None:
                    errors.append(
                        ContractError(
                            "DUPLICATE_UNIT_ID",
                            f"{location}.id",
                            f"unit ID {identifier!r} already appears at $.units[{first_index}]",
                        )
                    )
                else:
                    seen_ids[identifier] = index

            key = unit_key(item)
            if key is not None:
                units_by_key[key] = item
                unit_indexes[key] = index

        graph: dict[UnitKey, tuple[UnitKey, ...]] = {}
        resource_keys = declared["resources"]
        evidence_keys = declared["evidence_targets"]
        experiment_keys = declared["experiment_targets"]

        for index, item in unit_objects:
            location = f"$.units[{index}]"
            key = unit_key(item)
            dependencies: list[UnitKey] = []

            for ref_index, reference in enumerate(object_items(item.get("prerequisites"))):
                ref_location = f"{location}.prerequisites[{ref_index}]"
                errors.extend(self._owner_errors(reference, location=ref_location))
                ref_key = reference_key(reference)
                if ref_key is None:
                    continue
                target: UnitKey = (ref_key[1], ref_key[2], ref_key[3])
                if target not in units_by_key:
                    errors.append(
                        ContractError(
                            "UNRESOLVED_LEARNING_UNIT_REF",
                            ref_location,
                            f"no unit matches {ref_key[1]} {ref_key[2]} revision {ref_key[3]}",
                        )
                    )
                else:
                    dependencies.append(target)

            for ref_index, reference in enumerate(object_items(item.get("resources"))):
                ref_location = f"{location}.resources[{ref_index}]"
                errors.extend(self._owner_errors(reference, location=ref_location))
                ref_key = reference_key(reference)
                if ref_key is not None and ref_key not in resource_keys:
                    errors.append(
                        ContractError(
                            "UNRESOLVED_RESOURCE_REF",
                            ref_location,
                            f"no declared resource matches {ref_key[1]} {ref_key[2]} revision {ref_key[3]}",
                        )
                    )

            evidence = item.get("evidence_target")
            if isinstance(evidence, dict):
                evidence_location = f"{location}.evidence_target"
                errors.extend(self._owner_errors(evidence, location=evidence_location))
                evidence_key = reference_key(evidence)
                if evidence_key is not None and evidence_key not in evidence_keys:
                    errors.append(
                        ContractError(
                            "UNRESOLVED_EVIDENCE_REF",
                            evidence_location,
                            f"no declared Evidence target matches {evidence_key[2]} revision {evidence_key[3]}",
                        )
                    )

            for ref_index, reference in enumerate(object_items(item.get("experiment_refs"))):
                ref_location = f"{location}.experiment_refs[{ref_index}]"
                errors.extend(self._owner_errors(reference, location=ref_location))
                ref_key = reference_key(reference)
                if ref_key is not None and ref_key not in experiment_keys:
                    errors.append(
                        ContractError(
                            "UNRESOLVED_EXPERIMENT_REF",
                            ref_location,
                            f"no declared experiment matches {ref_key[2]} revision {ref_key[3]}",
                        )
                    )

            if key is not None:
                graph[key] = tuple(sorted(set(dependencies)))

        for cycle in self._cycles(graph):
            identifiers = ", ".join(node[1] for node in cycle)
            first_index = min(unit_indexes[node] for node in cycle)
            errors.append(
                ContractError(
                    "PREREQUISITE_CYCLE",
                    f"$.units[{first_index}].prerequisites",
                    f"prerequisite cycle detected among: {identifiers}",
                )
            )

        return ValidationResult(
            errors=tuple(sorted(set(errors))),
            unit_count=len(lists["units"]),
        )


def parse_expected_codes(value: JSONValue, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SuiteConfigurationError(f"{context} must be an array of strings")
    return tuple(sorted(cast(list[str], value)))


def run_suite(
    validator: LearningContractValidator,
    manifest_path: Path,
) -> tuple[bool, dict[str, JSONValue]]:
    manifest = require_object(load_json(manifest_path), context="suite manifest")
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise SuiteConfigurationError("suite manifest cases must be a non-empty array")

    case_results: list[dict[str, JSONValue]] = []
    suite_passed = True
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise SuiteConfigurationError(f"cases[{index}] must be an object")

        name = raw_case.get("name")
        catalog = raw_case.get("catalog")
        expected_exit = raw_case.get("expected_exit_code")
        if not isinstance(name, str) or not name:
            raise SuiteConfigurationError(f"cases[{index}].name must be a non-empty string")
        if not isinstance(catalog, str) or not catalog:
            raise SuiteConfigurationError(f"cases[{index}].catalog must be a non-empty string")
        if expected_exit not in (0, 1):
            raise SuiteConfigurationError(f"cases[{index}].expected_exit_code must be 0 or 1")

        expected_codes = parse_expected_codes(
            raw_case.get("expected_error_codes"),
            context=f"cases[{index}].expected_error_codes",
        )
        catalog_path = (manifest_path.parent / catalog).resolve()
        result = validator.validate_path(catalog_path)
        actual_exit = 0 if result.is_valid else 1
        actual_codes = result.error_codes
        passed = actual_exit == expected_exit and actual_codes == expected_codes
        suite_passed = suite_passed and passed
        case_results.append(
            {
                "name": name,
                "catalog": catalog,
                "status": "PASS" if passed else "FAIL",
                "expected_exit_code": expected_exit,
                "actual_exit_code": actual_exit,
                "expected_error_codes": list(expected_codes),
                "actual_error_codes": list(actual_codes),
            }
        )

    return suite_passed, cast(
        dict[str, JSONValue],
        {
            "status": "PASS" if suite_passed else "FAIL",
            "case_count": len(case_results),
            "cases": case_results,
        },
    )


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--catalog", type=Path, help="validate one catalog")
    selection.add_argument("--suite", type=Path, help="run an expected-result fixture suite")
    parser.add_argument(
        "--schema-dir",
        type=Path,
        default=repository_root() / "schemas" / "v2",
        help="directory containing the v2 schemas",
    )
    return parser


def emit(value: dict[str, JSONValue]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validator = LearningContractValidator.from_schema_dir(args.schema_dir)
        if args.catalog is not None:
            result = validator.validate_path(args.catalog)
            emit(result.as_json())
            return 0 if result.is_valid else 1

        suite_passed, suite_result = run_suite(validator, args.suite)
        emit(suite_result)
        return 0 if suite_passed else 1
    except (ContractLoadError, SuiteConfigurationError) as exc:
        emit(
            {
                "status": "ERROR",
                "error_codes": ["VALIDATOR_INPUT_ERROR"],
                "errors": [
                    {
                        "code": "VALIDATOR_INPUT_ERROR",
                        "location": "$",
                        "message": str(exc),
                    }
                ],
            }
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
