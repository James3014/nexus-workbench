# nexus-workbench

Transport-neutral, non-authority executable workbench contracts for Nexus professional agents.

Current status: **G1 protocol + persistence skeleton**. This repository does not yet execute repository actions.

## What G1 contains

- `WorkbenchNotebook` — durable facts, hypotheses, plan/progress and evidence references outside chat history.
- `ActionCell` — hash-bound, attributable executable proposal.
- `ObservationBundle` — hash-bound executor outcome with explicit `OUTCOME_UNKNOWN` support.
- `Executor` — transport-neutral protocol; no mandatory Dev MCP/DevSpace dependency.
- `JsonWorkbenchStore` — atomic durable JSON persistence with integrity checks.
- `Candidate` — evidence/result proposal whose claim ceiling is always `WORKBENCH_CANDIDATE_ONLY`.

G1 is governed by `SPEC-NEXUS-WORKBENCH-G0-20261006` and [Issue #1](https://github.com/James3014/nexus-workbench/issues/1).

## Authority

Workbench does not own routing, Planner decisions, Workforce Admission, verification, completion, acceptance, merge, release, deployment, or production claims. See [docs/AUTHORITY_BOUNDARY.md](docs/AUTHORITY_BOUNDARY.md).

## Verify

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Python 3.11+; G1 has no runtime dependencies outside the standard library.

## Next gate

G2 may add one isolated read-only repository executor only after G1 is independently verified. G2 must prove target source/worktree identity is unchanged and path-escape/write attempts fail closed.
