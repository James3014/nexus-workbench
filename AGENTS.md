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

## Handoff

`HANDOFF.md` at the repository root is the shared progress log for every agent (Claude, Codex, others) and every device. It records state only; it grants no authority and never overrides this file, Issues, or Task Cards.

- **Read first.** At session start read `HANDOFF.md`. If its recorded HEAD is behind, run `git log <recorded-HEAD>..HEAD` and correct stale entries before acting on them.
- **Update at milestones.** Update it when a meaningful step finishes or before the session ends — not every turn. Overwrite stale state; do not append history.
- **Format.** The header line holds date, author agent, and current short HEAD. `▶ 下一步` has exactly one item. Every todo carries an Issue number (`#N`); items without one go under `待確認`. Record why, blockers, and next — not changed-file lists (git already has those).
- **Size cap: 150 lines.** Move finished or expired content to `docs/archive/HANDOFF-YYYY-MM.md`.
- **Read back.** After writing, re-read `HANDOFF.md` to confirm the change actually landed.
- **No secrets.** Never put tokens, passwords, credentials, or internal hostnames in it.
