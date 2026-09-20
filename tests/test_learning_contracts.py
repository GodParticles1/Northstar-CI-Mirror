# pyright: strict

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_learning_contracts.py"
SCHEMA_DIR = ROOT / "schemas" / "v2"
FIXTURE_ROOT = ROOT / "testdata" / "learning-contracts" / "v2"
MANIFEST = FIXTURE_ROOT / "cases.json"


@dataclass(frozen=True)
class Case:
    name: str
    catalog: str
    expected_exit_code: int
    expected_error_codes: tuple[str, ...]


def load_cases() -> tuple[Case, ...]:
    raw = cast(dict[str, object], json.loads(MANIFEST.read_text(encoding="utf-8")))
    raw_cases = raw.get("cases")
    assert isinstance(raw_cases, list)
    cases: list[Case] = []
    for raw_value in cast(list[object], raw_cases):
        assert isinstance(raw_value, dict)
        value = cast(dict[str, object], raw_value)
        name = value.get("name")
        catalog = value.get("catalog")
        expected_exit = value.get("expected_exit_code")
        expected_codes = value.get("expected_error_codes")
        assert isinstance(name, str)
        assert isinstance(catalog, str)
        assert isinstance(expected_exit, int)
        assert isinstance(expected_codes, list)
        expected_code_values = cast(list[object], expected_codes)
        assert all(isinstance(code, str) for code in expected_code_values)
        cases.append(
            Case(
                name=name,
                catalog=catalog,
                expected_exit_code=expected_exit,
                expected_error_codes=tuple(sorted(cast(list[str], expected_codes))),
            )
        )
    return tuple(cases)


CASES = load_cases()


def run_validator(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--schema-dir",
            str(SCHEMA_DIR),
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def parse_output(process: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert process.stderr == ""
    return cast(dict[str, object], json.loads(process.stdout))


def test_expected_result_suite_passes() -> None:
    process = run_validator("--suite", str(MANIFEST))

    assert process.returncode == 0
    output = parse_output(process)
    assert output["status"] == "PASS"
    assert output["case_count"] == len(CASES)


def test_catalogs_have_stable_exit_and_error_codes() -> None:
    for case in CASES:
        process = run_validator("--catalog", str(FIXTURE_ROOT / case.catalog))

        assert process.returncode == case.expected_exit_code, case.name
        output = parse_output(process)
        error_codes = output.get("error_codes")
        assert isinstance(error_codes, list)
        error_code_values = cast(list[object], error_codes)
        assert all(isinstance(code, str) for code in error_code_values)
        assert tuple(error_code_values) == case.expected_error_codes, case.name
        expected_status = "PASS" if case.expected_exit_code == 0 else "FAIL"
        assert output["status"] == expected_status, case.name


def test_missing_catalog_is_a_stable_input_error(tmp_path: Path) -> None:
    process = run_validator("--catalog", str(tmp_path / "missing.json"))

    assert process.returncode == 2
    output = parse_output(process)
    assert output["status"] == "ERROR"
    assert output["error_codes"] == ["VALIDATOR_INPUT_ERROR"]
