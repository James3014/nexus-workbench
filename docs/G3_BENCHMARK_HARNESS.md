# G3 Deterministic A/B Benchmark Harness

G3 implements the benchmark gate from `SPEC-NEXUS-WORKBENCH-G0-20261006`.

## Purpose

The harness compares two arms on the same frozen engineering case:

- `BASELINE` — the incumbent agent workflow;
- `WORKBENCH` — the same model/provider/settings using the Workbench substrate.

The harness owns comparison mechanics only. It does not run providers, judge its own answers, or create Nexus verification/completion authority.

## Records

### BenchmarkCase

One frozen task definition bound to case id/category, provenance kind, source repository + exact Git revision, protocol version, frozen `task_input`, oracle requirements, and a finite evidence universe. The canonical `case_hash` covers all of those fields.

### BenchmarkRun

One arm trace bound to the exact `case_hash`, source/protocol, model identity, provider identity, model-settings hash, ordered tool/action signatures, evidence references, token counts, wall time, and a result reference. Changing the task input, oracle, or evidence universe invalidates the run-to-case binding.

The run schema intentionally has no correctness/completion/self-score fields.

### BenchmarkEvaluation

One external evaluation bound to an exact `run_hash`. It supplies root-cause correctness, task completion, false-conclusion count, evaluator identity, and notes reference. Changing a run invalidates the evaluator binding until a new evaluator record is produced.

## Fair comparison gate

A case is scored only when exactly one BASELINE and one WORKBENCH run exist and both have the same source revision, benchmark protocol version, model identity, provider identity, and model-settings hash.

Duplicate arms, missing arms, stale case hashes, stale evaluator hashes, unknown evidence, source drift, protocol drift, duplicate JSON object keys, negative counters, or malformed schemas fail closed.

## Metrics

The deterministic report includes root-cause correctness basis points, task-completion basis points, false conclusions, evidence coverage basis points, tool calls, repeated actions, total tokens, and wall time.

The per-case delta is always `WORKBENCH - BASELINE`. The report publishes the direction for each metric so lower-is-better metrics cannot be interpreted backwards. Aggregate means use exact rational strings rather than floating point.

## Determinism

The canonical report is one-line, key-sorted JSON. The checked smoke fixture must reproduce byte-for-byte across runs.

```bash
PYTHONPATH=src python -m nexus_workbench.benchmark validate benchmarks/fixtures/smoke_bundle.json
PYTHONPATH=src python -m nexus_workbench.benchmark report benchmarks/fixtures/smoke_bundle.json
```

## Synthetic smoke fixture

The bundled smoke corpus is intentionally synthetic. It exercises positive scoring, hand-checkable aggregates, model/provider/settings fairness rejection, source/protocol drift rejection, duplicate/missing arm rejection, evaluator subject-hash binding, evidence-universe containment, self-score field rejection, malformed/negative counter rejection, and CLI/canonical JSON determinism.

Its numerical deltas are not Workbench effectiveness evidence.

## Claim ceiling

`G3_BENCHMARK_HARNESS_VALIDATED_ONLY`

G3 does not prove that Workbench beats the baseline. A later real benchmark campaign must populate historical/frozen cases and independently evaluated runs before any performance claim.
