# Terraform

`modules/` holds module-per-concern building blocks (data-plane, identity, guardrails, agent, tools, observability). `envs/` holds per-environment compositions (`demo/`, `client-template/`).

Modules are account-agnostic and reusable. Anything Org / Control Tower–related belongs outside this tree. See `docs/repo-bootstrap-brief.md` §6 for the full layout and §15 for the reusability rules.
