# Guardrailed Agent — Repo Guide

This repo is the implementation of the ms3dm.tech *Guardrailed Agent over Governed Enterprise Data* flagship project.

## Read first
- `README.md` — current shipping status (Phases 1, 2, 3.a), live demo URL, repo layout
- `docs/repo-bootstrap-brief.md` — bootstrap brief; source of truth for Phases 1+2 scope, architecture, conventions
- `docs/phase-3a-brief.md` — Phase 3.a brief (public web demo: auth, gateway, web SPA, web-demo TF, DNS, smoke test)
- `ARCHITECTURE.md` — architectural deep-dive with ADR cross-links
- ADRs at `consulting/guardrailed-agent/decisions/` in the ms3dm.tech vault (if available locally)

## Non-negotiables
- Bedrock-native (Bedrock Agents + Bedrock Guardrails). Do not propose self-hosted alternatives.
- Per-client AWS account topology. Do not propose pooled SaaS or shared backends.
- ABAC session tags for Lake Formation. Do not propose hardcoded role ARNs in policy.
- Headless backend. The CLI is one client; Slack / web / mobile are future clients sharing the same surface.

## Conventions
- Terraform: HCL, modules-per-concern, env layer composes modules
- Python: 3.12, no comments unless the *why* is non-obvious
- Web (Phase 3.a): Vite + React 18 + TypeScript strict, Tailwind 3, Vitest + jsdom; pnpm 9 via corepack
- Tests: integration tests hit real AWS in the Demo account; no LF/Bedrock mocking
- Commits: small, focused; commit message starts with the area (`tf:`, `lambda:`, `lib:`, `web:`, `auth:`, `gateway:`, `data:`, `eval:`, `docs:`)
- No emojis in code, comments, or docs
- No backwards-compatibility cruft — this is a greenfield repo

## Module boundaries
- Lambdas in `lambdas/` keep business logic free of Bedrock-specific glue. The action group adapter (governed_query) and the gateway Lambda's `/ask` path are the only Bedrock-aware code.
- Terraform modules in `terraform/modules/` must be account-agnostic and reusable from any `terraform/envs/<name>/` with only variables.
- The agent backend is headless. Do not couple session state, conversation history, or auth to the CLI. The gateway Lambda is one of several surfaces (CLI, MCP, eval, SMUS notebook, web SPA, future Slack adapter) that all sit on top of `gagent_client.invoke()`.
- Persona resolution: `FlagPersonaResolver` (Shape A, single-operator), `SsoPersonaResolver` (Shape B, IAM Identity Center), `CognitoPersonaResolver` (Shape A or B for Cognito callers, mode flipped via `GAGENT_GATEWAY_PERSONA_RESOLUTION`). New surfaces consume the right resolver; they do not invent their own.

## Common operations
- Deploy demo: `./scripts/deploy-demo.sh`
- Seed data: `./scripts/seed-data.sh`
- Smoke test (CLI / Phases 1+2): `./scripts/smoke-test.sh`
- Smoke test (web / Phase 3.a): `SMOKE_TEST_JWT=<token> ./scripts/smoke-web.sh`
- Run eval: `cd eval && python runner.py`
- Build + deploy SPA: `cd web && pnpm build` then `aws s3 sync` + `aws cloudfront create-invalidation` (bucket name + distribution id are mirrored to `/gagent/demo/web_*` SSM parameters by Terraform)

## Don't do
- Don't add new top-level dependencies without an ADR
- Don't change the LF policy structure without an ADR
- Don't introduce a non-AWS managed service for any v1 component
- Don't hardcode account IDs, ARNs, or region-specific resources inside `terraform/modules/`
- Don't mock Lake Formation or Bedrock in tests; integration tests must run against real AWS in the Demo account
