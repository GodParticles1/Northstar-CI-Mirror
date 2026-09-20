# Beacon Proposal Compatibility v1alpha1

No silent acceptance or downgrade is allowed.

| Input | Supported value | Failure |
|---|---|---|
| ProposalEnvelope schema | `northstar.proposal-envelope/v1alpha1` | `SCHEMA_VERSION_UNSUPPORTED` |
| Contract | `northstar.beacon-proposal/v1alpha1` | `CONTRACT_VERSION_UNSUPPORTED` |
| ProposalRef schema | `northstar.proposal-ref/v1alpha1` | schema invalid / unsupported |
| Beacon producer schema | `beacon.proposal-draft/v1alpha1` | `PRODUCER_SCHEMA_UNSUPPORTED` |
| Producer repository | `GodParticles1/Beacon` | `PRODUCER_REPOSITORY_FORBIDDEN` |
| Producer revision | 40-hex Git revision shared by envelope and all Beacon refs | `PRODUCER_REVISION_INCOMPATIBLE` |

Repository-qualified refs owned by another repository reject as `CROSS_REPOSITORY_AUTHORITY_VIOLATION`.

Compatibility PASS means only that the envelope is valid for Northstar governance review. It is not governance acceptance and does not mutate any repository.
