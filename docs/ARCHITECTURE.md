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
        v
physical backend (future G2+)
```

G1 implements only protocol records, integrity binding, atomic JSON persistence, the executor interface, and Candidate claim containment. There is no physical repository executor in G1.

Process-local interpreter state is reconstructible cache. Durable Notebook/Action/Observation/Candidate data is explicit state and still does not become Nexus completion authority.
