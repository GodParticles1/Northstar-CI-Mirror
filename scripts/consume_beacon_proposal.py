#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "scripts/validate_beacon_proposal_contract.py"


def load_authority():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("beacon_proposal_validator", AUTHORITY)
    if spec is None or spec.loader is None:
        raise RuntimeError("validator import unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def machine(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def parse_single_input(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    if not raw.strip():
        return None, "INPUT_EMPTY"
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(raw.lstrip())
    except json.JSONDecodeError:
        return None, "INPUT_MALFORMED_JSON"
    if raw.lstrip()[end:].strip():
        return None, "INPUT_MULTIPLE_VALUES"
    if not isinstance(value, dict):
        return None, "SCHEMA_INVALID"
    return value, None


def consume(raw: str) -> tuple[int, dict[str, Any]]:
    envelope, input_error = parse_single_input(raw)
    if input_error is not None:
        return 2, {"status": "INVALID", "error": input_error, "governance_accepted": False}
    assert envelope is not None
    try:
        authority = load_authority()
        contract = authority.load(authority.CONTRACT)
        _, _, ref_validator, envelope_validator = authority.build_schema_validators()
        result = authority.validate_envelope(envelope, contract, ref_validator, envelope_validator)
    except Exception:
        return 3, {
            "status": "INVALID",
            "error": "VALIDATOR_INTERNAL_FAILURE",
            "governance_accepted": False,
        }
    if result != "VALID_FOR_GOVERNANCE_REVIEW":
        return 1, {"status": "INVALID", "error": result, "governance_accepted": False}
    return 0, {"status": result, "governance_accepted": False}


def main() -> int:
    code, result = consume(sys.stdin.read())
    machine(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
