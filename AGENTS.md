# Nexus Workbench repository contract

This repository owns a non-authority executable-workbench protocol and its standalone implementation.

## Authority boundary

- Do not create route, Planner, Workforce Admission, verification, completion, merge, release, deployment, or production authority here.
- Workbench may consume externally supplied task/scope/effect authority, preserve or narrow it, and produce Candidate/evidence artifacts only.
- Dev MCP/DevSpace must not be a mandatory dependency of the core protocol.
- Provider/session/connector identity is carrier evidence, never durable task authority.
- Resume worker/model identity is provenance only; it never replaces task/operation/attempt authority.

## Current gate

G4 only: durable cross-worker resume on top of G1 protocol/persistence, G2 isolated read-only execution, and G3 deterministic benchmarking.

G4 may define immutable resume checkpoints, durable heads, resume receipts, artifact records, monotonic Notebook persistence, immutable Action/Observation records, reconciliation-only handling for unresolved in-flight work, and fresh-process kill/restart canaries.

G4 does not authorize source mutation, arbitrary shell/Python, repository test execution by the product, live provider orchestration, Dev MCP/DevSpace dependency, remote-host/browser/vision execution, Nexus verification/completion authority, release/deployment, or production rollout.

`OUTCOME_UNKNOWN` is never retry permission. A resumed in-flight step must reconcile the same durable identity before any successor work.

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m compileall -q src tests
PYTHONPATH=src python -m nexus_workbench.benchmark validate benchmarks/fixtures/smoke_bundle.json
PYTHONPATH=src python -m nexus_workbench.benchmark report benchmarks/fixtures/smoke_bundle.json >/tmp/g3-report.json
cmp /tmp/g3-report.json benchmarks/fixtures/smoke_report.json
git diff --check
```
