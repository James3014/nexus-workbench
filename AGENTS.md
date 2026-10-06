# Nexus Workbench repository contract

This repository owns a non-authority executable-workbench protocol and its standalone implementation.

## Authority boundary

- Do not create route, Planner, Workforce Admission, verification, completion, merge, release, deployment, or production authority here.
- Workbench may consume externally supplied task/scope/effect authority, preserve or narrow it, and produce Candidate/evidence artifacts only.
- Dev MCP/DevSpace must not be a mandatory dependency of the core protocol.
- Provider/session/connector identity is carrier evidence, never durable task authority.

## Current gate

G2 only: standalone isolated read-only repository executor on top of the G1 protocol/persistence skeleton.

Allowed G2 target operations are fixed read-only probes: `repo.read`, `repo.search`, `git.status`, `git.diff`, and `test.discover`. Workbench may create artifacts only in a physically disjoint scratch root through `artifact.write`.

G2 does not authorize target-source mutation, arbitrary shell/Python execution, execution of repository test code, Dev MCP/DevSpace dependency, remote-host/browser/vision/multi-agent execution, G3 resume semantics, G4 benchmarking, or any Nexus authority expansion.

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m compileall -q src tests
git diff --check
```
