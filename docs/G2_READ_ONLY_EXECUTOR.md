# G2 Read-only Repository Executor

G2 implements `REQ-006` / `AC-005` from `SPEC-NEXUS-WORKBENCH-G0-20261006`.

## Goal

Provide one real executable Workbench backend that can inspect a bounded Git repository while proving the target source/worktree did not change.

## Binding

`LocalReadOnlyExecutor.bind(target_root, scratch_root)` requires:

1. the exact Git worktree root;
2. a clean target with no tracked, untracked, or ignored worktree state;
3. a scratch root physically disjoint from the target.

The bound target identity hashes HEAD, tree, Git status, and a full worktree content/mode manifest.

## Admission

Only these operations are admitted:

| Operation | Mode | Effect domain |
|---|---|---|
| `repo.read` | `READ_ONLY_PROBE` | target read |
| `repo.search` | `READ_ONLY_PROBE` | target read |
| `git.status` | `READ_ONLY_PROBE` | target read |
| `git.diff` | `READ_ONLY_PROBE` | target read |
| `test.discover` | `READ_ONLY_PROBE` | target read / AST parse |
| `artifact.write` | `PURE_COMPUTE` | Workbench scratch only |

The operation must also be declared in the Action Cell's `requested_capabilities`.

## Fail-closed controls

G2 blocks before target execution when:

- target identity no longer matches the bound baseline;
- the Action Cell is `EFFECTFUL`;
- operation/capability is unsupported or mismatched;
- a target path is absolute, contains `..`, or resolves outside the target;
- a symlink resolves outside the target;
- scratch overlaps the target.

After every action, G2 re-reads the target fingerprint. Any physical delta converts an otherwise successful action to `FAILED` and records changed target paths.

## Why tests are discovery-only

Running repository tests is arbitrary code execution and can mutate files, processes, network state, caches, or external systems. G2 therefore exposes AST-only `test.discover`, not `pytest`, shell, or Python execution.

A later wave may add actual test execution only behind a separately proven OS-level sandbox/authority contract.

## Canary evidence

The G2 fixture suite uses real temporary Git repositories rather than mocked Git calls. It exercises:

- allowed read/search/status/diff/test-discovery probes;
- top-level test code that would raise if imported, proving discovery does not execute it;
- scratch artifact creation outside target;
- attempted source write;
- parent and absolute path escape;
- tracked symlink escape;
- dirty target drift;
- clean moved HEAD drift;
- ignored-file physical mutation;
- capability substitution / unknown operation;
- injected post-action mutation detection;
- overlapping scratch-root rejection with zero target side effect.

Maximum claim: `G2_READ_ONLY_EXECUTOR_CANARY_ONLY`.
