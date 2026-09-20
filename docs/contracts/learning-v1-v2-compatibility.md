# Learning Contract v1/v2 Compatibility

Status: `ACCEPTED_FOR_PHASE_1`

## Boundary

Northstar `schemas/v1/` and the Stage 1 D01-D28 data/snapshots remain the historical Stage 1 contract. They are not reinterpreted, migrated, or generalized by Learning Contract v2.

Learning Contract v2 is an additive federated envelope for current learning-domain repositories (`Forge`, `Cortex`, `Nimbus`) and versioned references to `Proof` evidence and `Catalyst` experiments.

## Compatibility rules

- v1 and v2 coexist; v2 does not replace or mutate v1.
- Existing v1 IDs, files, snapshots, validators and consumers retain their original semantics.
- No automatic v1 -> v2 migration exists in Phase 1.
- A v2 `LearningUnitRef`, `ResourceRef`, `EvidenceRef` or `ExperimentRef` must not be synthesized from a v1 record merely because names appear similar.
- Proof remains mastery-evidence authority; Catalyst remains experiment authority. A v2 reference does not copy or upgrade the target's state.
- Referencing a Catalyst experiment is valid even when the experiment is `NOT_RUN`; the reference itself must never imply `PASS` or successful execution.

## Future migration gate

If a future phase needs to migrate or project v1 data into v2, it requires an explicit versioned mapping, migration plan, collision/ownership checks, rollback/correction strategy, fixed input/output evidence, and compatibility review. The migration must preserve v1 history rather than silently rewriting it.

## Evidence boundary

Passing the v2 validator proves only that the submitted v2 catalog satisfies the declared v2 schema and semantic checks. Existing Stage 1 regression passing proves the retained v1 implementation still passes its own validation. Neither result proves learner mastery or semantic equivalence between a v1 item and a v2 Knowledge Unit.
