from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "testdata/integration/beacon-proposal/BCN-P1-002-envelope.json"
VALIDATOR = ROOT / "scripts/validate_beacon_proposal_contract.py"

spec = importlib.util.spec_from_file_location("beacon_proposal_validator", VALIDATOR)
validator = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(validator)


class BeaconProposalFixedSHAIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact: dict[str, Any] = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        cls.envelope: dict[str, Any] = cls.artifact["envelope"]
        cls.contract = validator.load(validator.CONTRACT)
        _, _, cls.ref_validator, cls.envelope_validator = validator.build_schema_validators()

    def validate(self, envelope: dict[str, Any]) -> str:
        return validator.validate_envelope(
            envelope,
            self.contract,
            self.ref_validator,
            self.envelope_validator,
        )

    def changed(self, mutate) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        value = copy.deepcopy(self.envelope)
        mutate(value)
        value["canonical_payload_fingerprint"] = validator.fingerprint(value)
        return value

    def identity(self, envelope: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(envelope[field] for field in self.contract["identity"]["fields"])

    def test_fixed_identity_matches_actual_beacon_ci_output(self) -> None:
        self.assertEqual("GodParticles1/Beacon", self.artifact["producer_repository"])
        self.assertEqual(
            "6c6179c29647909c8adf47fadb67026004af8eec",
            self.artifact["producer_head"],
        )
        self.assertEqual(32222048567, self.artifact["producer_workflow_run_id"])
        self.assertEqual(self.artifact["producer_head"], self.envelope["producer_revision"])
        refs = [
            self.envelope["source_proposal_ref"],
            self.envelope["decision_ref"],
            *self.envelope["evidence_refs"],
        ]
        self.assertTrue(all(ref["producer_revision"] == self.artifact["producer_head"] for ref in refs))

    def test_actual_beacon_envelope_is_valid_for_governance_review_only(self) -> None:
        self.assertEqual("VALID_FOR_GOVERNANCE_REVIEW", self.validate(self.envelope))
        self.assertFalse(self.contract["acceptance_boundary"]["schema_valid_is_governance_accepted"])
        self.assertTrue(self.contract["acceptance_boundary"]["acceptance_requires_separate_governance_decision"])

    def test_actual_beacon_envelope_replay_is_deterministic(self) -> None:
        replay = json.loads(validator.canonical(self.envelope))
        self.assertEqual(validator.canonical(self.envelope), validator.canonical(replay))
        self.assertEqual(self.identity(self.envelope), self.identity(replay))
        self.assertEqual(validator.fingerprint(self.envelope), validator.fingerprint(replay))
        self.assertEqual("VALID_FOR_GOVERNANCE_REVIEW", self.validate(replay))
        self.assertEqual(
            "REPLAY_PRIOR_PROPOSAL",
            self.contract["identity"]["same_identity_same_payload"],
        )

    def test_changed_payload_same_identity_is_conflict(self) -> None:
        changed = self.changed(lambda value: value.__setitem__("created_at", "2026-08-17T00:00:01Z"))
        self.assertEqual("VALID_FOR_GOVERNANCE_REVIEW", self.validate(changed))
        self.assertEqual(self.identity(self.envelope), self.identity(changed))
        self.assertNotEqual(validator.fingerprint(self.envelope), validator.fingerprint(changed))
        self.assertEqual(
            "PROPOSAL_IDENTITY_CONFLICT",
            self.contract["identity"]["same_identity_changed_payload"],
        )

    def test_wrong_producer_revision_is_rejected(self) -> None:
        changed = self.changed(
            lambda value: value["source_proposal_ref"].__setitem__("producer_revision", "0" * 40)
        )
        self.assertEqual("PRODUCER_REVISION_INCOMPATIBLE", self.validate(changed))

    def test_wrong_repository_authority_is_rejected(self) -> None:
        changed = self.changed(
            lambda value: value["decision_ref"].__setitem__("repository", "GodParticles1/Northstar")
        )
        self.assertEqual("CROSS_REPOSITORY_AUTHORITY_VIOLATION", self.validate(changed))

    def test_wrong_producer_repository_is_rejected(self) -> None:
        changed = self.changed(
            lambda value: value.__setitem__("producer_repository", "GodParticles1/Northstar")
        )
        self.assertEqual("PRODUCER_REPOSITORY_FORBIDDEN", self.validate(changed))

    def test_missing_decision_ref_is_rejected(self) -> None:
        changed = copy.deepcopy(self.envelope)
        changed.pop("decision_ref")
        changed["canonical_payload_fingerprint"] = validator.fingerprint(changed)
        self.assertEqual("SCHEMA_INVALID", self.validate(changed))

    def test_invalid_evidence_ref_is_rejected(self) -> None:
        changed = self.changed(lambda value: value["evidence_refs"][0].__setitem__("object_id", "bad"))
        self.assertEqual("SCHEMA_INVALID", self.validate(changed))

    def test_incompatible_schema_and_contract_versions_are_rejected(self) -> None:
        bad_schema = self.changed(
            lambda value: value.__setitem__("schema_version", "northstar.proposal-envelope/v9")
        )
        self.assertEqual("SCHEMA_VERSION_UNSUPPORTED", self.validate(bad_schema))
        bad_contract = self.changed(
            lambda value: value.__setitem__("contract_version", "northstar.beacon-proposal/v9")
        )
        self.assertEqual("CONTRACT_VERSION_UNSUPPORTED", self.validate(bad_contract))


if __name__ == "__main__":
    unittest.main()
