# Authority boundary

Workbench is not a Planner, Router, Workforce Admission service, verifier, Completion Core, Candidate acceptance authority, merge authority, release authority, deployment authority, or production authority.

It may:

- consume externally supplied task/operation/scope/effect identities;
- preserve or narrow those ceilings in later effectful waves;
- maintain explicit professional reasoning state;
- bind actions to observations and artifacts;
- emit a Candidate with claim ceiling `WORKBENCH_CANDIDATE_ONLY`.

It may not:

- infer authority from a model response, connector identity, provider session, or local transport;
- widen a supplied capability/effect ceiling;
- turn model text such as `VERIFIED`, `MERGED`, or `DEPLOYED` into system truth;
- require Dev MCP/DevSpace as a core protocol dependency.

G1 is deliberately pre-executor. Real read-only execution belongs to G2.
