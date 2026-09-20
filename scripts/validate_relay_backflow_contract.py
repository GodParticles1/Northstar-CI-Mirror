"""Offline SourceRef / LearningNeedEnvelope checks; no source resolution or writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
VALID = "VALID_FOR_TARGET_REVIEW"
MAX_INTEGER = 9007199254740991
IDENTITY_FIELDS = ("need_id", "revision", "producer_repository", "target_repository")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_number(value: str) -> Any:
    raise ValueError("non-integer JSON number")


def _canonical_domain(value: Any) -> None:
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif type(value) is int:
        if not -MAX_INTEGER <= value <= MAX_INTEGER:
            raise ValueError("integer outside canonical range")
    elif isinstance(value, list):
        for item in value:
            _canonical_domain(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("non-string key")
            _canonical_domain(key)
            _canonical_domain(item)
    elif value is not None and type(value) is not bool:
        raise ValueError("unsupported canonical value")


def load_json(path: Path) -> Any:
    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_pairs,
        parse_float=_invalid_number, parse_constant=_invalid_number,
    )
    _canonical_domain(value)
    return value


def canonical_payload(envelope: dict[str, Any]) -> bytes:
    """Only omit the top-level digest; array order and exact Unicode are significant."""
    payload = {key: value for key, value in envelope.items() if key != "payload_sha256"}
    _canonical_domain(payload)
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def payload_sha256(envelope: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(envelope)).hexdigest()


def build_validators() -> tuple[Draft202012Validator, Draft202012Validator]:
    source = load_json(ROOT / "schemas/v1alpha1/source-ref.schema.json")
    envelope = load_json(ROOT / "schemas/v1alpha1/learning-need-envelope.schema.json")
    Draft202012Validator.check_schema(source)
    Draft202012Validator.check_schema(envelope)
    # Registry has only the local schema; no retrieve callback / remote resolution.
    registry = Registry().with_resource(source["$id"], Resource.from_contents(source))
    return Draft202012Validator(source), Draft202012Validator(envelope, registry=registry)


def validate_source(value: Any) -> str:
    try:
        _canonical_domain(value)
    except (TypeError, ValueError, UnicodeError):
        return "CANONICAL_INPUT_INVALID"
    source_validator, _ = build_validators()
    if not source_validator.is_valid(value):
        return "SOURCE_SCHEMA_INVALID"
    # Detect obvious commit-only identities. Producers remain responsible for stability.
    suffix = value["source_id"].rsplit(":", 1)[-1]
    if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", suffix):
        return "SOURCE_ID_NOT_LOGICAL"
    locator = value.get("locator", {})
    if locator.get("kind") == "line_range":
        match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", locator["value"])
        if match is None or int(match[1]) > int(match[2]):
            return "SOURCE_LOCATOR_INVALID"
    return "VALID_SOURCE_REF"


def _target_result(repository: str, registry: Any) -> str:
    if not isinstance(registry, dict) or not isinstance(registry.get("repositories"), list):
        return "REGISTRY_INVALID"
    seen: set[str] = set()
    target = None
    for row in registry["repositories"]:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key), str) or not row[key]
            for key in ("repository", "profile", "status")
        ):
            return "REGISTRY_INVALID"
        name = row["repository"]
        if name.casefold() in seen:
            return "REGISTRY_INVALID"
        seen.add(name.casefold())
        if name == repository:
            target = row
    if target is None:
        return "TARGET_NOT_REGISTERED"
    if target["status"] != "active":
        return "TARGET_NOT_ACTIVE"
    if target["profile"] != "learning-domain":
        return "TARGET_NOT_LEARNING_DOMAIN"
    return VALID


def validate_envelope(value: Any, registry: Any = None) -> str:
    try:
        _canonical_domain(value)
    except (TypeError, ValueError, UnicodeError):
        return "CANONICAL_INPUT_INVALID"
    _, envelope_validator = build_validators()
    if not envelope_validator.is_valid(value):
        return "ENVELOPE_SCHEMA_INVALID"
    if type(value["revision"]) is not int:
        return "ENVELOPE_SCHEMA_INVALID"
    for binding in value["sources"]:
        result = validate_source(binding["source_ref"])
        if result != "VALID_SOURCE_REF":
            return result
    if registry is None:
        registry = load_json(ROOT / "data/repositories/registry.json")
    result = _target_result(value["target_repository"], registry)
    if result != VALID:
        return result
    if value["payload_sha256"] != payload_sha256(value):
        return "PAYLOAD_SHA256_MISMATCH"
    return VALID


def replay_result(prior: Any, incoming: Any, registry: Any = None) -> str:
    """Pure pairwise comparison; no persistence, dedupe store or disposition state."""
    for value in (prior, incoming):
        result = validate_envelope(value, registry)
        if result != VALID:
            return result
    if tuple(prior[key] for key in IDENTITY_FIELDS) != tuple(
        incoming[key] for key in IDENTITY_FIELDS
    ):
        return "NEW_IDENTITY"
    return "REPLAY" if canonical_payload(prior) == canonical_payload(incoming) else "IDENTITY_CONFLICT"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--prior", type=Path, help="Compare with a previous envelope without writing")
    args = parser.parse_args(argv)
    try:
        value = load_json(args.input)
        is_source = isinstance(value, dict) and value.get("kind") == "SourceRef"
        if is_source:
            status = "INPUT_INVALID" if args.prior else validate_source(value)
        else:
            status = validate_envelope(value)
        result: dict[str, Any] = {"status": status}
        if status == VALID:
            result.update(governance_accepted=False, target_accepted=False, mastery_proven=False)
            if args.prior:
                replay = replay_result(load_json(args.prior), value)
                result["replay"] = replay
                if replay not in {"REPLAY", "NEW_IDENTITY"}:
                    result["status"] = replay
    except (OSError, ValueError, UnicodeError, RecursionError):
        result = {"status": "INPUT_INVALID"}
    except Exception:  # noqa: BLE001 - CLI trust boundary must fail closed
        # Fail closed on trusted schema/config faults; do not echo source content.
        result = {"status": "VALIDATOR_ERROR"}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] in {VALID, "VALID_SOURCE_REF"} else 1


if __name__ == "__main__":
    sys.exit(main())
