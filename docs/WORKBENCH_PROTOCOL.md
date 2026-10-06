# Workbench protocol v1

G1 defines four hash-bound records.

## WorkbenchNotebook

Durable task reasoning state independent of chat transcript and provider session. Includes task/operation/attempt/target identity, facts, hypotheses, plan/progress, unresolved items, and observation/artifact references.

## ActionCell

One attributable proposed step. `PURE_COMPUTE`, `READ_ONLY_PROBE`, and future `EFFECTFUL` are representable, but the G1 gate rejects `EFFECTFUL` execution through `assert_g1_allowed()`.

## ObservationBundle

One executor result bound to exact session, step, and Action Cell hash. `OUTCOME_UNKNOWN` is first-class and is not normalized to success. Read-only verification can reject any non-empty `changed_target_paths`.

## Candidate

A proposed result/evidence package. Its immutable maximum claim is `WORKBENCH_CANDIDATE_ONLY`; model prose cannot promote it.

## Persistence

`JsonWorkbenchStore` uses contained identifiers, atomic replace, file fsync, directory fsync, and content-hash verification on reload. It stores no transcript/provider-session authority.
