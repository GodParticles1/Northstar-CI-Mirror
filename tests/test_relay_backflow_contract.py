from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import inspect
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch


def cases(names: str | tuple[str, ...], values: Any) -> Callable[..., Any]:
    """Parameterize unittest cases without adding a Stage 1 discovery dependency."""
    def decorate(function: Any) -> Any:
        function.variants = [(dict(zip(names, row)) if isinstance(names, tuple)
                              else {names: row}) for row in values]
        return function
    return decorate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_relay_backflow_contract.py"
spec = importlib.util.spec_from_file_location("backflow", SCRIPT)
assert spec is not None and spec.loader is not None
backflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backflow)
FIXTURES = ROOT / "testdata/backflow"


def envelope() -> dict[str, Any]:
    return backflow.load_json(FIXTURES / "valid-envelope.json")


def sealed(value: dict[str, Any]) -> dict[str, Any]:
    value["payload_sha256"] = backflow.payload_sha256(value)
    return value


def registry() -> dict[str, Any]:
    return backflow.load_json(ROOT / "data/repositories/registry.json")


def check_external_sources_and_unregistered_producer() -> None:
    value = envelope()
    before = copy.deepcopy(value)
    members = {row["repository"] for row in registry()["repositories"]}
    assert value["producer_repository"] not in members
    for filename in ("canonical-source.json", "historical-source.json"):
        source = backflow.load_json(FIXTURES / filename)
        assert source["repository"] not in members
        assert backflow.validate_source(source) == "VALID_SOURCE_REF"
    assert backflow.validate_envelope(value) == backflow.VALID
    assert value == before


@cases("target", ["Forge", "Cortex", "Nimbus"])
def check_current_learning_targets(target: str) -> None:
    value = envelope()
    value["target_repository"] = "GodParticles1/" + target
    assert backflow.validate_envelope(sealed(value)) == backflow.VALID


@cases(("target", "expected"), [
    ("GodParticles1/Unregistered", "TARGET_NOT_REGISTERED"),
    ("GodParticles1/Relay", "TARGET_NOT_REGISTERED"),
    ("GodParticles1/Proof", "TARGET_NOT_LEARNING_DOMAIN"),
    ("GodParticles1/Aegis", "TARGET_NOT_LEARNING_DOMAIN"),
    ("GodParticles1/Northstar", "TARGET_NOT_LEARNING_DOMAIN"),
])
def check_target_authority(target: str, expected: str) -> None:
    value = envelope()
    value["target_repository"] = target
    assert backflow.validate_envelope(sealed(value)) == expected


def check_future_registered_domain_is_not_hardcoded() -> None:
    value = envelope()
    value["target_repository"] = "Example/SecurityLearning"
    reg = registry()
    reg["repositories"].append({"repository": value["target_repository"],
                                "profile": "learning-domain", "status": "active"})
    assert backflow.validate_envelope(sealed(value), reg) == backflow.VALID
    reg["repositories"][-1]["status"] = "retired"
    assert backflow.validate_envelope(value, reg) == "TARGET_NOT_ACTIVE"


@cases("bad", [None, [], {}, {"repositories": [{}]}])
def check_registry_fails_closed(bad: Any) -> None:
    assert backflow._target_result("GodParticles1/Forge", bad) == "REGISTRY_INVALID"


def check_duplicate_registry_identity_is_rejected() -> None:
    reg = registry()
    duplicate = copy.deepcopy(reg["repositories"][3])
    duplicate["repository"] = duplicate["repository"].lower()
    reg["repositories"].append(duplicate)
    assert backflow.validate_envelope(envelope(), reg) == "REGISTRY_INVALID"


@cases("sha", ["a" * 39, "a" * 41, "g" * 40, "main", "a" * 40 + "\n", 1])
def check_bad_source_sha(sha: Any) -> None:
    value = envelope()
    value["sources"][0]["source_ref"]["commit_sha"] = sha
    assert backflow.validate_envelope(sealed(value)) == "ENVELOPE_SCHEMA_INVALID"


@cases("path", ["/etc/passwd", "../x", "a/../b", "a/./b", ".", "..",
                                    "a//b", "a/", "C:/x", "a\\b", "a\x00b", "a\nb"])
def check_bad_source_path(path: str) -> None:
    value = envelope()
    value["sources"][0]["source_ref"]["path"] = path
    assert backflow.validate_envelope(sealed(value)) == "ENVELOPE_SCHEMA_INVALID"


@cases("field", ["sources", "objective", "rationale", "acceptance", "does_not_prove"])
def check_empty_required_content(field: str) -> None:
    value = envelope()
    value[field] = [] if isinstance(value[field], list) else "  \t"
    assert backflow.validate_envelope(sealed(value)) == "ENVELOPE_SCHEMA_INVALID"


@cases("index", range(4))
def check_each_boundary_is_mandatory(index: int) -> None:
    value = envelope()
    value["does_not_prove"].pop(index)
    value["does_not_prove"].append("other limitation")
    assert backflow.validate_envelope(sealed(value)) == "ENVELOPE_SCHEMA_INVALID"


@cases("revision", [0, -1, True, "1", 1.0, 9007199254740992])
def check_revision_is_positive_bounded_integer(revision: Any) -> None:
    value = envelope()
    value["revision"] = revision
    assert backflow.validate_envelope(value) != backflow.VALID


@cases("field", ["EvidenceRef", "learning_unit", "mastery", "target_accepted"])
def check_no_invented_ref_or_state(field: str) -> None:
    value = envelope()
    value[field] = "synthetic"
    assert backflow.validate_envelope(sealed(value)) == "ENVELOPE_SCHEMA_INVALID"


def check_replay_conflict_and_revision() -> None:
    prior = envelope()
    reordered = dict(reversed(list(prior.items())))
    assert backflow.replay_result(prior, reordered) == "REPLAY"
    changed = copy.deepcopy(prior)
    changed["sources"][0]["source_ref"]["commit_sha"] = "c" * 40
    assert backflow.validate_envelope(changed) == "PAYLOAD_SHA256_MISMATCH"
    assert backflow.replay_result(prior, changed) == "PAYLOAD_SHA256_MISMATCH"
    assert backflow.replay_result(prior, sealed(changed)) == "IDENTITY_CONFLICT"
    changed["revision"] += 1
    assert backflow.validate_envelope(sealed(changed)) == backflow.VALID
    assert backflow.replay_result(prior, changed) == "NEW_IDENTITY"
    assert prior["need_id"] == changed["need_id"]
    assert prior["sources"][0]["source_ref"]["source_id"] == changed["sources"][0]["source_ref"]["source_id"]


@cases(("field", "new"), [("need_id", "fixture:other"),
                                           ("producer_repository", "Other/Producer"),
                                           ("target_repository", "GodParticles1/Nimbus")])
def check_all_identity_components(field: str, new: str) -> None:
    prior = envelope()
    value = copy.deepcopy(prior)
    value[field] = new
    assert backflow.replay_result(prior, sealed(value)) == "NEW_IDENTITY"


@cases("status", ["confirmed_controlled_test", "strong_inference", "unresolved", "future-native:未核实"])
def check_source_status_is_opaque(status: str) -> None:
    value = envelope()
    value["sources"][0]["source_ref"]["source_status"] = status
    sealed(value)
    before = copy.deepcopy(value)
    assert backflow.validate_envelope(value) == backflow.VALID
    assert value == before


def check_locator_and_commit_only_source_id() -> None:
    value = backflow.load_json(FIXTURES / "canonical-source.json")
    for locator in ["0-2", "2-1", "1", "1-2\n"]:
        value["locator"] = {"kind": "line_range", "value": locator}
        assert backflow.validate_source(value) == "SOURCE_LOCATOR_INVALID"
    value["locator"]["value"] = "1-2"
    assert backflow.validate_source(value) == "VALID_SOURCE_REF"
    value["source_id"] = "source:" + "a" * 40
    assert backflow.validate_source(value) == "SOURCE_ID_NOT_LOGICAL"


def check_canonical_fixed_vector_and_unicode() -> None:
    vector = backflow.load_json(FIXTURES / "canonical-vector.json")
    assert backflow.canonical_payload(vector["input"]) == vector["canonical_utf8"].encode()
    assert backflow.payload_sha256(vector["input"]) == vector["sha256"]
    value = envelope()
    value["objective"] = "解释 e\u0301 与 é"
    sealed(value)
    serialized = json.dumps(value, ensure_ascii=True, indent=4)
    assert backflow.replay_result(value, json.loads(serialized)) == "REPLAY"
    reordered = copy.deepcopy(value)
    reordered["sources"].reverse()
    assert backflow.replay_result(value, sealed(reordered)) == "IDENTITY_CONFLICT"
    # NFC/NFD remain different; canonicalization does not normalize source text.
    changed = copy.deepcopy(value)
    changed["objective"] = "解释 é 与 é"
    assert backflow.replay_result(value, sealed(changed)) == "IDENTITY_CONFLICT"


@cases("raw", ['{"kind":"SourceRef","kind":"LearningNeedEnvelope"}',
                                 '{"revision":1.0}', '{"revision":NaN}',
                                 '{"revision":Infinity}', '{"x":"\\ud800"}',
                                 '{} {}', '[]', ''])
def check_cli_malformed_input(raw: str, tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text(raw)
    run = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, check=False)
    assert run.returncode == 1
    assert json.loads(run.stdout)["status"] != backflow.VALID
    assert not run.stderr


def check_cli_valid_replay_and_conflict(tmp_path: Path) -> None:
    prior = FIXTURES / "valid-envelope.json"
    run = subprocess.run([sys.executable, str(SCRIPT), str(prior), "--prior", str(prior)],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert json.loads(run.stdout) == {"status": backflow.VALID, "replay": "REPLAY",
                                    "governance_accepted": False, "target_accepted": False,
                                    "mastery_proven": False}
    value = envelope()
    value["objective"] = "Understand a different bounded concept."
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(sealed(value)))
    run = subprocess.run([sys.executable, str(SCRIPT), str(path), "--prior", str(prior)],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 1
    assert json.loads(run.stdout)["status"] == "IDENTITY_CONFLICT"


def check_validator_does_not_resolve_sources_execute_or_write(tmp_path: Path) -> None:
    value = envelope()
    marker = tmp_path / "must-not-exist"
    value["sources"][0]["summary"] = "$(touch " + str(marker) + ")"
    sealed(value)
    with patch("socket.create_connection", side_effect=AssertionError("network")), \
         patch("subprocess.run", side_effect=AssertionError("execution")), \
         patch.object(Path, "write_text", side_effect=AssertionError("write")), \
         patch.object(Path, "write_bytes", side_effect=AssertionError("write")):
        assert backflow.validate_envelope(value) == backflow.VALID
    assert not marker.exists()


def check_internal_failure_is_closed() -> None:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), patch.object(
        backflow, "build_validators", side_effect=RuntimeError("private detail")
    ):
        assert backflow.main([str(FIXTURES / "valid-envelope.json")]) == 1
    assert json.loads(output.getvalue()) == {"status": "VALIDATOR_ERROR"}


def check_legacy_files_are_byte_identical() -> None:
    manifest = backflow.load_json(FIXTURES / "compatibility-baseline.json")
    assert manifest["base_sha"] == "c06a507b20775e074a0fc9c0579458d6c8443dc1"
    for path, expected in manifest["git_blobs"].items():
        data = (ROOT / path).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        assert actual == expected, path


class BackflowContractTests(unittest.TestCase):
    """Run the same parameterized cases under pytest and existing unittest discovery."""


def _method(function: Callable[..., object], params: dict[str, Any]) -> Callable[..., None]:
    def run(self: unittest.TestCase) -> None:
        arguments = dict(params)
        with tempfile.TemporaryDirectory(prefix="backflow-test-") as directory:
            if "tmp_path" in inspect.signature(function).parameters:
                arguments["tmp_path"] = Path(directory)
            function(**arguments)
    return run


for _name, _function in list(globals().items()):
    if _name.startswith("check_") and callable(_function):
        for _index, _params in enumerate(getattr(_function, "variants", [{}])):
            setattr(BackflowContractTests, "test_" + _name[6:] + f"_{_index}",
                    _method(_function, _params))


if __name__ == "__main__":
    unittest.main()
