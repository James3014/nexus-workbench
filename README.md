# nexus-workbench

Transport-neutral, non-authority executable workbench contracts for Nexus professional agents.

Current status: **G3 deterministic A/B benchmark harness**.

## What exists

G1 established durable Notebook/Action/Observation/Candidate contracts, hash-bound persistence, and a transport-neutral Executor protocol.

G2 added `LocalReadOnlyExecutor`, bound to one exact clean Git target and a physically disjoint scratch root, with fixed read-only repository probes and physical before/after readback.

G3 adds a deterministic, provider-neutral A/B benchmark harness:

- immutable benchmark cases;
- BASELINE and WORKBENCH run traces;
- external evaluator records bound to exact run hashes;
- strict same-source/model/provider/settings fairness gates;
- deterministic per-case and aggregate metrics;
- canonical byte-stable JSON reports;
- synthetic positive/negative smoke fixtures.

Synthetic fixture results validate the harness only. They are not evidence that Workbench improves real engineering performance.

## G3 benchmark CLI

```bash
PYTHONPATH=src python3 -m nexus_workbench.benchmark validate benchmarks/fixtures/smoke_bundle.json
PYTHONPATH=src python3 -m nexus_workbench.benchmark report benchmarks/fixtures/smoke_bundle.json
```

The report tracks root-cause correctness, completion, false conclusions, evidence coverage, tool calls, repeated actions, tokens and wall time. Correctness/completion come only from separate evaluator records; an agent cannot self-score through its run trace.

See:

- [Architecture](docs/ARCHITECTURE.md)
- [Authority boundary](docs/AUTHORITY_BOUNDARY.md)
- [Workbench protocol](docs/WORKBENCH_PROTOCOL.md)
- [G2 read-only executor](docs/G2_READ_ONLY_EXECUTOR.md)
- [G3 benchmark harness](docs/G3_BENCHMARK_HARNESS.md)
- [G3 Issue #10](https://github.com/James3014/nexus-workbench/issues/10)

## Verify

Requires Python 3.11+ and no runtime dependencies outside the standard library.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 -m nexus_workbench.benchmark validate benchmarks/fixtures/smoke_bundle.json
PYTHONPATH=src python3 -m nexus_workbench.benchmark report benchmarks/fixtures/smoke_bundle.json >/tmp/g3-report.json
cmp /tmp/g3-report.json benchmarks/fixtures/smoke_report.json
git diff --check
```

## Claim ceiling

Passing G3 verification supports only:

`G3_BENCHMARK_HARNESS_VALIDATED_ONLY`

It does not prove Workbench effectiveness on real Nexus tasks, G4 cross-worker resume, mutation safety, integrated Nexus consumption, runtime deployment, or production readiness.
