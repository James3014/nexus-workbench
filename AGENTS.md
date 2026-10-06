# Nexus Workbench repository contract

This repository owns a non-authority executable-workbench protocol and its standalone implementation.

## Authority boundary

- Do not create route, Planner, Workforce Admission, verification, completion, merge, release, deployment, or production authority here.
- Workbench may consume externally supplied task/scope/effect authority, preserve or narrow it, and produce Candidate/evidence artifacts only.
- Dev MCP/DevSpace must not be a mandatory dependency of the core protocol.
- Provider/session/connector identity is carrier evidence, never durable task authority.
- Benchmark evaluator records are evidence about a run, not Nexus completion/acceptance authority.

## Current gate

G3 only: deterministic A/B benchmark harness on top of the G1 protocol/persistence skeleton and G2 isolated read-only repository executor.

G3 may define immutable benchmark cases, BASELINE/WORKBENCH run traces, external evaluator records, fairness gates, deterministic metrics, and canonical reports.

G3 does not authorize live provider orchestration, source mutation, arbitrary shell/Python, repository test execution, Dev MCP/DevSpace dependency, remote-host/browser/vision execution, G4 durable cross-worker resume, or any Nexus authority expansion.

Synthetic fixture results validate the harness only. They must never be cited as Workbench effectiveness evidence.

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m compileall -q src tests
PYTHONPATH=src python -m nexus_workbench.benchmark validate benchmarks/fixtures/smoke_bundle.json
PYTHONPATH=src python -m nexus_workbench.benchmark report benchmarks/fixtures/smoke_bundle.json >/tmp/g3-report.json
cmp /tmp/g3-report.json benchmarks/fixtures/smoke_report.json
git diff --check
```
