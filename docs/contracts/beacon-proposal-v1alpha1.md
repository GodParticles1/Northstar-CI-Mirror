# Northstar ↔ Beacon Proposal Contract v1alpha1

Status: `CONTRACT_GATE / NOT_RUNTIME_INTEGRATION`

Task: `NS-P1-001`

Machine authority: `docs/contracts/v1alpha1/beacon-proposal.contract.json`, `schemas/v1alpha1/proposal-ref.schema.json`, and `schemas/v1alpha1/proposal-envelope.schema.json`.

## Authority boundary

Beacon remains the unique owner of `Source`, `Signal`, `TechnologyCandidate`, `Evaluation`, `Decision`, and `ProposalDraft`. Northstar does not copy those objects. Northstar owns only the repository-qualified `ProposalRef`, `ProposalEnvelope`, compatibility/version rules, deterministic validation, and the separate governance accept/reject boundary.

## ProposalEnvelope

The envelope carries `schema_version`, `contract_version`, stable proposal identity/revision, producer repository/revision, proposal type, source ProposalDraft ref, Decision ref, Evidence refs, creation time, compatibility facts, and a canonical payload fingerprint. Refs are opaque/repository-qualified and producer-revision aware.

## Replay

Identity is `(proposal_id, proposal_revision, producer_repository)`. Canonical payload is deterministic JSON excluding its own fingerprint field, hashed with SHA-256.

- same identity + same canonical payload -> `REPLAY_PRIOR_PROPOSAL`;
- same identity + changed payload -> `PROPOSAL_IDENTITY_CONFLICT`.

Replay is an identity property, not governance acceptance.

## Acceptance boundary

- Beacon `ProposalDraft` emitted != Northstar Proposal accepted.
- Northstar schema/semantic validation PASS != governance acceptance.
- Northstar governance acceptance != automatic write/change in Aegis, Forge, Cortex, Nimbus, Proof, Catalyst, or Beacon.

A valid envelope is only `VALID_FOR_GOVERNANCE_REVIEW` until a separate governance decision exists.

## Coupling boundary

No hidden shared state, cross-repository database, raw filesystem coupling, or copied Beacon domain payload is permitted by this contract.

## Evidence boundary

Contract/fixture CI can prove deterministic envelope/ref/version/replay/authority behavior only. It does not prove Beacon producer integration, cross-repository transport, governance acceptance, live Connector behavior, or production operation.
