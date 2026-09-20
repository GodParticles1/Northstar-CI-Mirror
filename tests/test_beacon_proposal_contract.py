import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("v", ROOT / "scripts/validate_beacon_proposal_contract.py")
v = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(v)


class TestProposalContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = v.load(v.CONTRACT)
        cls.suite = v.load(v.FIXTURES)
        cls.ref_schema, cls.envelope_schema, cls.ref_validator, cls.envelope_validator = v.build_schema_validators()
        cls.cases = {case["id"]: case for case in cls.suite["cases"]}

    def evaluate(self, case_id):
        return v.evaluate(self.cases[case_id], self.contract, self.suite["producer_revision"], self.ref_validator, self.envelope_validator)

    def test_count_preserves_original_semantics_plus_divergence_regression(self):
        self.assertEqual(15, len(self.cases))

    def test_valid_envelope_and_proposal_ref_pass_authoritative_schemas(self):
        envelope = v.base(self.suite["producer_revision"])
        self.assertEqual([], v.schema_errors(self.ref_validator, envelope["source_proposal_ref"]))
        self.assertEqual([], v.schema_errors(self.envelope_validator, envelope))
        self.assertEqual(self.ref_schema["$id"], self.envelope_schema["properties"]["source_proposal_ref"]["$ref"])

    def test_schema_divergence_additional_property_is_rejected(self):
        self.assertEqual("SCHEMA_INVALID", self.evaluate("schema-divergence-additional-property"))
        envelope = v.mutate(v.base(self.suite["producer_revision"]), "additional_property")
        errors = v.schema_errors(self.envelope_validator, envelope)
        self.assertTrue(errors)
        self.assertEqual("additionalProperties", errors[0].validator)

    def test_valid_not_accepted(self):
        self.assertEqual("VALID_FOR_GOVERNANCE_REVIEW", self.evaluate("valid-envelope"))
        self.assertFalse(self.contract["acceptance_boundary"]["schema_valid_is_governance_accepted"])

    def test_replay(self): self.assertEqual("REPLAY_PRIOR_PROPOSAL", self.evaluate("same-payload-replay"))
    def test_conflict(self): self.assertEqual("PROPOSAL_IDENTITY_CONFLICT", self.evaluate("changed-payload-conflict"))
    def test_owner(self): self.assertEqual("CROSS_REPOSITORY_AUTHORITY_VIOLATION", self.evaluate("wrong-authority-owner"))
    def test_revision(self): self.assertEqual("PRODUCER_REVISION_INCOMPATIBLE", self.evaluate("incompatible-revision"))
    def test_versions(self):
        self.assertEqual("SCHEMA_VERSION_UNSUPPORTED", self.evaluate("invalid-schema-version"))
        self.assertEqual("CONTRACT_VERSION_UNSUPPORTED", self.evaluate("unsupported-contract-version"))
    def test_serialization(self): self.assertEqual("DETERMINISTIC", self.evaluate("deterministic-serialization"))


if __name__ == "__main__":
    unittest.main()
