# Workbench protocol v1

G1 defines four hash-bound records; G2 adds one concrete read-only executor; G3 adds a deterministic benchmark contract. None changes Nexus authority semantics.

## WorkbenchNotebook

Durable task reasoning state independent of chat transcript and provider session. Includes task/operation/attempt/target identity, facts, hypotheses, plan/progress, unresolved items, and observation/artifact references.

## ActionCell

One attributable proposed step. `PURE_COMPUTE`, `READ_ONLY_PROBE`, and future `EFFECTFUL` are representable.

G2 interprets `code_or_action` as exactly one JSON object with an `op` field. The operation must also appear in `requested_capabilities`.

Repository probes require `READ_ONLY_PROBE`. Scratch artifact creation requires `PURE_COMPUTE`. `EFFECTFUL` is blocked.

## ObservationBundle

One executor result bound to exact session, step, and Action Cell hash. `OUTCOME_UNKNOWN` is first-class and is not normalized to success.

G2 physical readback includes baseline/before/after target snapshots and the operation result. Any detected target delta appears in `changed_target_paths`; a successful action that changes target identity is converted to `FAILED`.

## G2 fixed operation surface

Target-facing:

- `repo.read` — UTF-8 file read with containment and size limit.
- `repo.search` — bounded literal text search without following symlinks.
- `git.status` — fixed Git status invocation.
- `git.diff` — fixed staged/unstaged Git diff invocation with external diff/textconv disabled.
- `test.discover` — AST-only test discovery; does not import or execute repository code.

Scratch-only:

- `artifact.write` — atomic text artifact write under a physically disjoint Workbench scratch root.

No G2 action accepts arbitrary shell commands, arbitrary Git arguments, arbitrary Python, source writes, or test execution.

## G3 benchmark contract

G3 introduces separate benchmark records:

- `BenchmarkCase` freezes case provenance, source revision, protocol, oracle requirements, and evidence universe.
- `BenchmarkRun` records arm identity, model/provider/settings binding, ordered actions, evidence references, token counts, latency, and a result reference. It contains no correctness/completion fields.
- `BenchmarkEvaluation` supplies external scoring and binds the exact run hash.
- `BenchmarkBundle` pairs cases, runs, and evaluator records.
- `build_report` refuses unfair or incomplete pairs and emits exact rational aggregate metrics plus per-case deltas.

The accepted pair requires the same source revision, protocol version, model identity, provider identity, and model-settings hash across BASELINE and WORKBENCH arms.

Synthetic fixture reports always remain under `G3_BENCHMARK_HARNESS_VALIDATED_ONLY`; they are not effectiveness evidence.

## Candidate

A proposed result/evidence package. Its immutable maximum claim is `WORKBENCH_CANDIDATE_ONLY`; model prose cannot promote it.

## Persistence

`JsonWorkbenchStore` uses contained identifiers, atomic replace, file fsync, directory fsync, and content-hash verification on reload. It stores no transcript/provider-session authority.
