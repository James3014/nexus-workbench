# G4 Durable Cross-Worker Resume

G4 implements the durable-resume gate from `SPEC-NEXUS-WORKBENCH-G0-20261006`.

## Purpose

G4 lets a Workbench Session continue after the original interpreter, process, worker, model, provider session, or chat context is gone. Resume truth comes only from explicit durable Workbench records.

## Durable state

`ResumeCheckpoint` binds the exact Workbench session/task/operation/attempt/target identity, Notebook revision + content hash, ordered completed steps, Observation and Artifact lineage, phase, and either the exact next Action Cell or the exact in-flight Action Cell.

`ResumeHead` is the durable pointer to one immutable checkpoint revision. `JsonWorkbenchStore` serializes resume publication under a per-session file lock and only advances the head when the successor sequence and predecessor id/hash match the current durable head. Durable multi-part identities use nested path components rather than delimiter-joined filenames, preventing cross-session/step aliasing.

Notebook writes are monotonic by revision. Action Cells, Observation Bundles, and Artifact Records are immutable-idempotent by durable identity: the same bytes/content may be re-observed, but a conflicting overwrite fails closed.

## Resume phases

- `READY_FOR_ACTION`: before a fresh worker receives the exact durable next Action Cell, resume atomically claims the current head, persists that Action Cell, and advances the durable head to `IN_FLIGHT`. A competing resumer therefore receives reconciliation, not a second READY claim.
- `IN_FLIGHT`: a fresh worker receives `RECONCILE_REQUIRED`; it never blindly resends the action.
- `RECONCILE_REQUIRED`: the same exact step remains a reconciliation gate; G4 does not self-authorize a successor.
- `COMPLETE`: there is no continuation work.

`OUTCOME_UNKNOWN` is never retry permission. It remains reconciliation-only.

## Validation

`ResumeCoordinator` reloads the durable head, complete checkpoint lineage, Notebook, completed Action/Observation pairs, and Artifact Records. It rejects stale or conflicting session/task/operation/attempt/target identity, Notebook revision/hash drift, missing references, action/observation substitution, ambiguous durable path identity, lineage gaps/forks, completed-step replay, tampered hashes, duplicate JSON keys, and any G4 `EFFECTFUL` Action. Artifact lineage is append-only, and every Artifact reference declared by a completed Observation must be present in the checkpoint/Notebook lineage and resolve durably.

Worker and model identities appear only on the resume receipt as provenance. They do not become durable task authority.

## Kill/restart canary

The G4 canary launches a producer Python process (`worker-A` / `model-A`) that writes durable Notebook, Action, Observation, Artifact, and READY checkpoint state. The producer is terminated. A different Python process (`worker-B` / `model-B`) reconstructs the next Action Cell from durable state only, without transcript/provider-session input. A target fixture is hashed before and after and must remain unchanged.

## Authority boundary

G4 remains transport-neutral and non-authoritative. It does not add routing, verification, completion, acceptance, merge, release, deployment, mutation, provider, or production authority. Dev MCP/DevSpace is not a core dependency.

## Claim ceiling

`G4_DURABLE_RESUME_CANARY_ONLY`

Passing G4 proves only the standalone durable-resume contract and kill/restart canary. It does not prove Workbench effectiveness, effectful mutation safety, remote execution, Nexus integration, runtime deployment, or production readiness.
