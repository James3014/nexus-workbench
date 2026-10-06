# Nexus Workbench repository contract

This repository owns a non-authority executable-workbench protocol and its standalone implementation.

## Authority boundary

- Do not create route, Planner, Workforce Admission, verification, completion, merge, release, deployment, or production authority here.
- Workbench may consume externally supplied task/scope/effect authority, preserve or narrow it, and produce Candidate/evidence artifacts only.
- Dev MCP/DevSpace must not be a mandatory dependency of the core protocol.
- Provider/session/connector identity is carrier evidence, never durable task authority.

## Current gate

G1 only: protocol + persistence skeleton. No real repository executor, no target-source mutation, no remote-host/browser/vision/multi-agent execution.

## Verification

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```
