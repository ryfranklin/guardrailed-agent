# Guardrailed Agent — Repo Guide

This repo is the implementation of the ms3dm.tech *Guardrailed Agent over Governed Enterprise Data* flagship project.

## Read first
- `docs/repo-bootstrap-brief.md` — the full bootstrap brief; the source of truth for Phase 1 scope, architecture, and conventions
- `ARCHITECTURE.md` — architectural decision summary with ADR cross-links
- ADRs at `consulting/guardrailed-agent/decisions/` in the ms3dm.tech vault (if available locally)

## Non-negotiables
- Bedrock-native (Bedrock Agents + Bedrock Guardrails). Do not propose self-hosted alternatives.
- Per-client AWS account topology. Do not propose pooled SaaS or shared backends.
- ABAC session tags for Lake Formation. Do not propose hardcoded role ARNs in policy.
- Headless backend. The CLI is one client; Slack / web / mobile are future clients sharing the same surface.

## Conventions
- Terraform: HCL, modules-per-concern, env layer composes modules
- Python: 3.12, no comments unless the *why* is non-obvious
- Tests: integration tests hit real AWS in the Demo account; no LF/Bedrock mocking
- Commits: small, focused; commit message starts with the area (`tf:`, `lambda:`, `data:`, `eval:`, `docs:`)
- No emojis in code, comments, or docs
- No backwards-compatibility cruft — this is a greenfield repo

## Module boundaries
- Lambdas in `lambdas/` keep business logic free of Bedrock-specific glue. The action group adapter is the only Bedrock-aware code.
- Terraform modules in `terraform/modules/` must be account-agnostic and reusable from any `terraform/envs/<name>/` with only variables.
- The agent backend is headless. Do not couple session state, conversation history, or auth to the CLI.

## Common operations
- Deploy demo: `./scripts/deploy-demo.sh`
- Seed data: `./scripts/seed-data.sh`
- Smoke test: `./scripts/smoke-test.sh`
- Run eval: `cd eval && python runner.py`

## Don't do
- Don't add new top-level dependencies without an ADR
- Don't change the LF policy structure without an ADR
- Don't introduce a non-AWS managed service for any v1 component (Langfuse is the only exception, scoped to dev/eval)
- Don't hardcode account IDs, ARNs, or region-specific resources inside `terraform/modules/`
- Don't mock Lake Formation or Bedrock in tests; integration tests must run against real AWS in the Demo account
