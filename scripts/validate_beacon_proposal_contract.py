#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/contracts/v1alpha1/beacon-proposal.contract.json"
REF_SCHEMA = ROOT / "schemas/v1alpha1/proposal-ref.schema.json"
ENV_SCHEMA = ROOT / "schemas/v1alpha1/proposal-envelope.schema.json"
FIXTURES = ROOT / "testdata/contracts/beacon-proposal/v1alpha1/cases.json"
REQUIRED = {"valid-envelope", "same-payload-replay", "changed-payload-conflict", "invalid-schema-version", "unsupported-contract-version", "malformed-proposal-ref", "missing-source-proposal-ref", "missing-decision-ref", "invalid-evidence-ref", "wrong-producer-repository", "wrong-authority-owner", "incompatible-revision", "deterministic-serialization", "cross-repository-authority-violation", "schema-divergence-additional-property"}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: dict[str, Any]) -> str:
    payload = copy.deepcopy(value); payload.pop("canonical_payload_fingerprint", None)
    return "sha256:" + hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def build_schema_validators() -> tuple[dict[str, Any], dict[str, Any], Draft202012Validator, Draft202012Validator]:
    ref_schema = load(REF_SCHEMA); envelope_schema = load(ENV_SCHEMA)
    Draft202012Validator.check_schema(ref_schema); Draft202012Validator.check_schema(envelope_schema)
    registry = Registry().with_resource(ref_schema["$id"], Resource.from_contents(ref_schema))
    return ref_schema, envelope_schema, Draft202012Validator(ref_schema), Draft202012Validator(envelope_schema, registry=registry)


def schema_errors(validator: Draft202012Validator, value: Any) -> list[ValidationError]:
    return sorted(validator.iter_errors(value), key=lambda err: tuple(str(part) for part in err.absolute_path))


def base(revision: str) -> dict[str, Any]:
    envelope: dict[str, Any] = {"schema_version":"northstar.proposal-envelope/v1alpha1","contract_version":"northstar.beacon-proposal/v1alpha1","proposal_id":"nsp_beacon-prop-001","proposal_revision":1,"producer_repository":"GodParticles1/Beacon","producer_revision":revision,"proposal_type":"TECHNOLOGY_PROPOSAL","source_proposal_ref":{"schema_version":"northstar.proposal-ref/v1alpha1","repository":"GodParticles1/Beacon","object_type":"ProposalDraft","object_id":"prop_fixture-001","object_contract_version":"beacon.proposal-draft/v1alpha1","producer_revision":revision},"decision_ref":{"repository":"GodParticles1/Beacon","object_type":"Decision","object_id":"dec_fixture-001","object_contract_version":"beacon.decision/v1alpha1","producer_revision":revision},"evidence_refs":[{"repository":"GodParticles1/Beacon","object_type":"Evaluation","object_id":"eval_fixture-001","object_contract_version":"beacon.evaluation/v1alpha1","producer_revision":revision}],"created_at":"2026-08-19T01:02:03Z","compatibility":{"compatibility_version":"northstar.beacon-proposal/v1alpha1","producer_schema_version":"beacon.proposal-draft/v1alpha1"},"canonical_payload_fingerprint":""}
    envelope["canonical_payload_fingerprint"] = fingerprint(envelope); return envelope


def mutate(envelope: dict[str, Any], name: str) -> dict[str, Any]:
    value = copy.deepcopy(envelope)
    if name == "none": return value
    if name == "changed_payload": value["created_at"] = "2026-08-19T01:02:04Z"
    elif name == "invalid_schema_version": value["schema_version"] = "northstar.proposal-envelope/v9"
    elif name == "unsupported_contract_version": value["contract_version"] = "northstar.beacon-proposal/v9"
    elif name == "malformed_proposal_ref": value["source_proposal_ref"]["object_id"] = "bad"
    elif name == "missing_source_ref": value.pop("source_proposal_ref")
    elif name == "missing_decision_ref": value.pop("decision_ref")
    elif name == "invalid_evidence_ref": value["evidence_refs"][0]["object_id"] = "bad"
    elif name == "wrong_producer": value["producer_repository"] = "GodParticles1/Northstar"
    elif name == "wrong_owner": value["decision_ref"]["repository"] = "GodParticles1/Northstar"
    elif name == "incompatible_revision": value["source_proposal_ref"]["producer_revision"] = "0" * 40
    elif name == "authority_violation": value["source_proposal_ref"]["repository"] = "GodParticles1/Aegis"
    elif name == "additional_property": value["unexpected_validation_gap"] = True
    elif name == "reorder": return {key: value[key] for key in reversed(list(value))}
    else: raise ValueError(name)
    value["canonical_payload_fingerprint"] = fingerprint(value); return value


def map_schema_error(error: ValidationError, contract: dict[str, Any]) -> str:
    path = list(error.absolute_path)
    if path == ["schema_version"]: return contract["compatibility"]["unsupported_schema_error"]
    if path == ["contract_version"]: return contract["compatibility"]["unsupported_contract_error"]
    if path == ["producer_repository"]: return contract["producer"]["wrong_producer_error"]
    if path == ["compatibility", "producer_schema_version"]: return contract["compatibility"]["unsupported_producer_schema_error"]
    if path and path[0] in {"source_proposal_ref", "decision_ref", "evidence_refs"} and "repository" in path: return contract["authority"]["wrong_owner_error"]
    return "SCHEMA_INVALID"


def structural_error(envelope: dict[str, Any], contract: dict[str, Any], ref_validator: Draft202012Validator, envelope_validator: Draft202012Validator) -> str | None:
    errors = schema_errors(envelope_validator, envelope)
    if errors: return map_schema_error(errors[0], contract)
    if schema_errors(ref_validator, envelope["source_proposal_ref"]): return "SCHEMA_INVALID"
    return None


def semantic_result(envelope: dict[str, Any], contract: dict[str, Any]) -> str:
    if envelope["schema_version"] not in contract["compatibility"]["supported_envelope_schema_versions"]: return contract["compatibility"]["unsupported_schema_error"]
    if envelope["contract_version"] not in contract["compatibility"]["supported_contract_versions"]: return contract["compatibility"]["unsupported_contract_error"]
    if envelope["producer_repository"] != contract["producer"]["repository"]: return contract["producer"]["wrong_producer_error"]
    refs = [envelope["source_proposal_ref"], envelope["decision_ref"], *envelope["evidence_refs"]]
    if any(ref["repository"] != contract["producer"]["repository"] for ref in refs): return contract["authority"]["wrong_owner_error"]
    if any(ref["producer_revision"] != envelope["producer_revision"] for ref in refs): return contract["compatibility"]["incompatible_revision_error"]
    if envelope["compatibility"]["producer_schema_version"] not in contract["compatibility"]["supported_producer_schema_versions"]: return contract["compatibility"]["unsupported_producer_schema_error"]
    if envelope["canonical_payload_fingerprint"] != fingerprint(envelope): return "PAYLOAD_FINGERPRINT_MISMATCH"
    return contract["acceptance_boundary"]["validation_result"]


def validate_envelope(envelope: dict[str, Any], contract: dict[str, Any], ref_validator: Draft202012Validator, envelope_validator: Draft202012Validator) -> str:
    error = structural_error(envelope, contract, ref_validator, envelope_validator)
    return error if error is not None else semantic_result(envelope, contract)


def static_checks(contract: dict[str, Any], ref_schema: dict[str, Any], envelope_schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(contract["authority"]["northstar_owned_objects"]) != {"ProposalRef", "ProposalEnvelope"}: errors.append("Northstar owner set")
    if set(contract["authority"]["beacon_owned_objects"]) != {"Source", "Signal", "TechnologyCandidate", "Evaluation", "Decision", "ProposalDraft"}: errors.append("Beacon owner set")
    if contract["authority"]["beacon_domain_payload_copied"] is not False: errors.append("domain copy")
    if contract["compatibility"]["silent_acceptance"] is not False: errors.append("silent acceptance")
    if any(contract["coupling"].values()): errors.append("hidden coupling")
    boundary = contract["acceptance_boundary"]
    if boundary["emitted_is_accepted"] or boundary["schema_valid_is_governance_accepted"] or boundary["governance_acceptance_mutates_member_repositories"]: errors.append("acceptance collapse")
    if ref_schema.get("properties", {}).get("repository", {}).get("const") != "GodParticles1/Beacon": errors.append("ref owner")
    if envelope_schema.get("properties", {}).get("producer_repository", {}).get("const") != "GodParticles1/Beacon": errors.append("envelope owner")
    if envelope_schema.get("properties", {}).get("source_proposal_ref", {}).get("$ref") != ref_schema.get("$id"): errors.append("source_proposal_ref does not use authoritative ProposalRef schema")
    return errors


def evaluate(case: dict[str, Any], contract: dict[str, Any], revision: str, ref_validator: Draft202012Validator, envelope_validator: Draft202012Validator) -> str:
    prior = base(revision); incoming = mutate(prior, case["mutation"])
    if case["operation"] == "serialize": return "DETERMINISTIC" if canonical(incoming) == canonical(json.loads(canonical(incoming))) else "NON_DETERMINISTIC"
    if case["operation"] == "validate": return validate_envelope(incoming, contract, ref_validator, envelope_validator)
    if case["operation"] == "replay":
        result = validate_envelope(incoming, contract, ref_validator, envelope_validator)
        if result != "VALID_FOR_GOVERNANCE_REVIEW": return result
        identity = lambda value: (value["proposal_id"], value["proposal_revision"], value["producer_repository"])
        if identity(prior) != identity(incoming): return "NOT_DUPLICATE_IDENTITY"
        return contract["identity"]["same_identity_same_payload"] if fingerprint(prior) == fingerprint(incoming) else contract["identity"]["same_identity_changed_payload"]
    raise ValueError(case["operation"])


def main() -> int:
    try:
        contract = load(CONTRACT); ref_schema, envelope_schema, ref_validator, envelope_validator = build_schema_validators(); suite = load(FIXTURES)
        errors = static_checks(contract, ref_schema, envelope_schema); ids = [case["id"] for case in suite["cases"]]
        if len(ids) != len(set(ids)) or set(ids) != REQUIRED: errors.append("fixture set mismatch")
        valid = base(suite["producer_revision"])
        if schema_errors(ref_validator, valid["source_proposal_ref"]): errors.append("valid ProposalRef rejected by authoritative schema")
        if schema_errors(envelope_validator, valid): errors.append("valid ProposalEnvelope rejected by authoritative schema")
        for case in suite["cases"]:
            actual = evaluate(case, contract, suite["producer_revision"], ref_validator, envelope_validator)
            if actual != case["expected"]: errors.append(f"{case['id']}: expected {case['expected']} got {actual}")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, SchemaError) as exc:
        print("PROPOSAL_CONTRACT_INPUT_ERROR:", exc, file=sys.stderr); return 2
    if errors:
        for error in errors: print("PROPOSAL_CONTRACT_ERROR:", error, file=sys.stderr)
        return 1
    print("BEACON_PROPOSAL_CONTRACT_PASS " f"fixtures={len(ids)} schema_examples=2 contract={contract['contract_version']}"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
