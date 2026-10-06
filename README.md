# nexus-workbench

Transport-neutral, non-authority executable workbench contracts for Nexus professional agents.

Current status: **G4 durable cross-worker resume**.

## What exists

- **G1**: durable Notebook/Action/Observation/Candidate contracts and transport-neutral executor protocol.
- **G2**: isolated local read-only repository executor with physical before/after target readback.
- **G3**: deterministic provider-neutral A/B benchmark harness with external evaluator records and strict fairness gates.
- **G4**: durable resume checkpoints and a fresh-process resume coordinator that reconstructs the next gate from persisted state rather than chat/model memory.

## G4 resume invariants

- Resume is bound to exact session/task/operation/attempt/target identity.
- Checkpoints bind exact Notebook revision/hash plus completed Observation and Artifact lineage.
- Notebook revisions advance monotonically.
- Action, Observation, and Artifact records are immutable-idempotent by durable identity.
- `READY_FOR_ACTION` may expose only the exact durable next Action Cell when it has not already materialized.
- `IN_FLIGHT` and `OUTCOME_UNKNOWN` resume only as `RECONCILE_REQUIRED`; they never authorize blind replay.
- Original transcript, provider session, model identity, process memory, and interpreter globals are not required for resume.

The G4 kill/restart canary terminates a producer process and resumes from a separate process with different worker/model labels while keeping the target fixture unchanged.

See:

- [Architecture](docs/ARCHITECTURE.md)
- [Authority boundary](docs/AUTHORITY_BOUNDARY.md)
- [Workbench protocol](docs/WORKBENCH_PROTOCOL.md)
- [G2 read-only executor](docs/G2_READ_ONLY_EXECUTOR.md)
- [G3 benchmark harness](docs/G3_BENCHMARK_HARNESS.md)
- [G4 durable resume](docs/G4_DURABLE_RESUME.md)
- [G4 Issue #13](https://github.com/James3014/nexus-workbench/issues/13)

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

Passing G4 verification supports only:

`G4_DURABLE_RESUME_CANARY_ONLY`

It does not prove Workbench effectiveness, effectful mutation safety, remote execution, integrated Nexus consumption, runtime deployment, or production readiness.
