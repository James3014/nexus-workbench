# Architecture

Nexus Workbench is a non-authority consumer between Nexus execution contracts and replaceable physical executors.

```text
Nexus governance / operation authority
        |
        v
nexus-runtime identities / effect ceilings
        |
        v
nexus-workbench
  Notebook -> Action Cell -> Observation -> Candidate
        |
        v
Executor protocol
        |
        +--> LocalReadOnlyExecutor (G2)
        |
        +--> future replaceable backends
```

G1 established protocol records, integrity binding, durable JSON persistence, the executor interface, and Candidate claim containment.

G2 implements one standalone local read-only executor. It binds one exact clean Git worktree plus a disjoint scratch root, admits only fixed read-only repository probes, and physically compares target identity before and after every Action Cell.

G3 adds a measurement layer beside execution rather than adding execution authority:

```text
frozen BenchmarkCase
      |
      +--> BASELINE run trace ----+
      |                           |
      +--> WORKBENCH run trace ---+--> exact-run evaluator records
                                  |
                                  v
                         fairness / pairing gate
                                  |
                                  v
                         deterministic report
```

Run traces contain observed behavior and cost only. Correctness/completion are supplied by separate evaluator records bound to exact run hashes. Synthetic fixtures validate the harness but cannot establish Workbench effectiveness.

The physical target fingerprint includes:

- Git `HEAD`;
- `HEAD^{tree}`;
- porcelain Git status including untracked and ignored paths;
- a content/mode manifest of the worktree filesystem excluding only the top-level `.git` metadata.

This makes prompt-level "read only" insufficient by design: G2 requires physical readback.

Process-local interpreter state remains reconstructible cache. Durable Notebook/Action/Observation/Candidate data remains explicit state and does not become Nexus completion authority.
