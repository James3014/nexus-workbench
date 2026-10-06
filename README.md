# nexus-workbench

Transport-neutral, non-authority executable workbench contracts for Nexus professional agents.

Current status: **G2 isolated read-only repository executor**.

## What exists

G1 established:

- `WorkbenchNotebook` — durable facts, hypotheses, plan/progress and evidence references outside chat history.
- `ActionCell` — hash-bound, attributable executable proposal.
- `ObservationBundle` — hash-bound executor outcome with explicit `OUTCOME_UNKNOWN` support.
- `Executor` — transport-neutral protocol; no mandatory Dev MCP/DevSpace dependency.
- `JsonWorkbenchStore` — atomic durable JSON persistence with integrity checks.
- `Candidate` — evidence/result proposal whose claim ceiling is always `WORKBENCH_CANDIDATE_ONLY`.

G2 adds `LocalReadOnlyExecutor`, bound to one exact clean Git target and a physically disjoint scratch root.

Its target-facing surface is deliberately fixed:

- `repo.read`
- `repo.search`
- `git.status`
- `git.diff`
- `test.discover`

`artifact.write` is allowed only in Workbench-owned scratch outside the target repository.

Every action compares before/after target HEAD, tree, Git status, and a physical worktree manifest that includes tracked, untracked, and ignored filesystem state while excluding only the worktree's top-level `.git` metadata.

## G2 safety boundary

G2 fails closed on:

- `EFFECTFUL` Action Cells;
- unsupported or substituted capabilities;
- absolute and parent path escapes;
- symlink escapes;
- dirty, moved, or otherwise stale target identity;
- scratch roots that overlap the target;
- any detected post-action target mutation.

G2 does **not** expose arbitrary shell, arbitrary Python execution, repository test execution, source writes, Dev MCP/DevSpace integration, remote-host control, browser/GUI/vision, or completion authority.

See:

- [Architecture](docs/ARCHITECTURE.md)
- [Authority boundary](docs/AUTHORITY_BOUNDARY.md)
- [Workbench protocol](docs/WORKBENCH_PROTOCOL.md)
- [G2 read-only executor](docs/G2_READ_ONLY_EXECUTOR.md)
- [G2 Issue #6](https://github.com/James3014/nexus-workbench/issues/6)

## Verify

Requires Python 3.11+ and no runtime dependencies outside the standard library.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src tests
git diff --check
```

## Claim ceiling

Passing G2 verification supports only:

`G2_READ_ONLY_EXECUTOR_CANARY_ONLY`

It does not prove G3 cross-worker resume, G4 A/B effectiveness, mutation safety, integrated Nexus consumption, runtime deployment, or production readiness.
