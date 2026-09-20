from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = ROOT / "scripts/consume_beacon_proposal.py"
AUTHORITY = ROOT / "scripts/validate_beacon_proposal_contract.py"
FIXED_BEACON_SHA = "4d628efc8c5fc5e7385fc67d696d18b04749ec3b"


def load_module(name: str, path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


consumer = load_module("beacon_consumer", CONSUMER)
validator = load_module("beacon_authority", AUTHORITY)


class BeaconProposalConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid = validator.base(FIXED_BEACON_SHA)

    def consume(self, value) -> tuple[int, dict]:  # type: ignore[no-untyped-def]
        raw = value if isinstance(value, str) else validator.canonical(value)
        return consumer.consume(raw)

    def changed(self, mutate, recompute: bool = True) -> dict:  # type: ignore[no-untyped-def]
        value = copy.deepcopy(self.valid)
        mutate(value)
        if recompute:
            value["canonical_payload_fingerprint"] = validator.fingerprint(value)
        return value

    def test_happy_path_is_review_only(self) -> None:
        code, result = self.consume(self.valid)
        self.assertEqual(0, code)
        self.assertEqual("VALID_FOR_GOVERNANCE_REVIEW", result["status"])
        self.assertFalse(result["governance_accepted"])

    def test_malformed_json(self) -> None:
        self.assertEqual("INPUT_MALFORMED_JSON", self.consume("{")[1]["error"])

    def test_empty_input(self) -> None:
        self.assertEqual("INPUT_EMPTY", self.consume("   \n")[1]["error"])

    def test_multiple_values(self) -> None:
        raw = validator.canonical(self.valid) + "\n" + validator.canonical(self.valid)
        code, result = self.consume(raw)
        self.assertNotEqual(0, code)
        self.assertEqual("INPUT_MULTIPLE_VALUES", result["error"])

    def test_wrong_producer(self) -> None:
        value = self.changed(lambda x: x.__setitem__("producer_repository", "GodParticles1/Northstar"))
        self.assertEqual("PRODUCER_REPOSITORY_FORBIDDEN", self.consume(value)[1]["error"])

    def test_revision_mismatch(self) -> None:
        value = self.changed(lambda x: x["source_proposal_ref"].__setitem__("producer_revision", "0" * 40))
        self.assertEqual("PRODUCER_REVISION_INCOMPATIBLE", self.consume(value)[1]["error"])

    def test_decision_ref_failure(self) -> None:
        value = copy.deepcopy(self.valid)
        value.pop("decision_ref")
        value["canonical_payload_fingerprint"] = validator.fingerprint(value)
        self.assertEqual("SCHEMA_INVALID", self.consume(value)[1]["error"])

    def test_evidence_ref_failure(self) -> None:
        value = self.changed(lambda x: x["evidence_refs"][0].__setitem__("object_id", "bad"))
        self.assertEqual("SCHEMA_INVALID", self.consume(value)[1]["error"])

    def test_unsupported_contract_version(self) -> None:
        value = self.changed(lambda x: x.__setitem__("contract_version", "northstar.beacon-proposal/v9"))
        self.assertEqual("CONTRACT_VERSION_UNSUPPORTED", self.consume(value)[1]["error"])

    def test_fingerprint_mismatch(self) -> None:
        value = self.changed(lambda x: x.__setitem__("created_at", "2026-08-19T01:02:04Z"), recompute=False)
        self.assertEqual("PAYLOAD_FINGERPRINT_MISMATCH", self.consume(value)[1]["error"])

    def test_validator_internal_failure_is_never_valid(self) -> None:
        with mock.patch.object(consumer, "load_authority", side_effect=RuntimeError("injected")):
            code, result = consumer.consume(validator.canonical(self.valid))
        self.assertEqual(3, code)
        self.assertEqual("VALIDATOR_INTERNAL_FAILURE", result["error"])
        self.assertNotEqual("VALID_FOR_GOVERNANCE_REVIEW", result["status"])


if __name__ == "__main__":
    unittest.main()
