# auth

Cognito User Pool, Hosted UI domain, and app client for the Phase 3.a
public web demo. The Hosted UI surfaces email/password sign-in only;
federated identity providers (Google, GitHub, Slack) are deferred to
Phase 3.5 — see the comment in `main.tf` for the rationale.

The pool fronts a single SPA client (`generate_secret = false`) and is
consumed by:

- API Gateway HTTP API JWT authorizer (`jwt_issuer_url`, `user_pool_client_id`)
- SPA Amplify Auth config (`user_pool_id`, `user_pool_client_id`,
  `hosted_ui_domain`)

Source-of-truth specs: [phase-3a-brief §7](../../../docs/phase-3a-brief.md#7-cognito-user-pool-config-the-auth-module)
and [ADR-007](../../../../consulting/guardrailed-agent/decisions/007-multi-user-identity-federation.md).

## Inputs

| Variable | Required | Default | Notes |
|---|---|---|---|
| `env` | yes | — | Environment name (e.g. `demo`). |
| `name_prefix` | no | `gagent-` | Pool name = `<prefix><env>`. |
| `hosted_ui_domain_prefix` | no | `<name_prefix><env>` | Globally-unique Cognito-managed prefix. |
| `callback_urls` | no | `demo.ms3dm.tech/auth/callback`, `localhost:5173/auth/callback` | Cognito callback URLs. |
| `logout_urls` | no | `demo.ms3dm.tech/`, `localhost:5173/` | Cognito logout URLs. |
| `id_token_validity_minutes` | no | 30 | ADR-007 set 60; lowered to 30 by [ADR-013](../../../../consulting/guardrailed-agent/decisions/013-abuse-rate-limit-posture.md) §5.3. |
| `access_token_validity_minutes` | no | 30 | ADR-007 set 60; lowered to 30 by [ADR-013](../../../../consulting/guardrailed-agent/decisions/013-abuse-rate-limit-posture.md) §5.3. |
| `refresh_token_validity_days` | no | 30 | Per ADR-007; ADR-013 §5.3 explicitly preserves the refresh window for UX. |
| `github_client_id` / `github_client_secret` | no (sensitive) | `""` | Reserved for the Phase 3.5 GitHub-as-OIDC reintroduction. Not consumed by any resource today. |
| `tags` | no | `{}` | Common tags. |

Secrets are passed via `TF_VAR_<name>` or a gitignored `terraform.tfvars`.
Never commit them.

## Outputs

| Output | Notes |
|---|---|
| `user_pool_id` | Cognito user pool ID. |
| `user_pool_arn` | Pool ARN. |
| `user_pool_endpoint` | `cognito-idp.<region>.amazonaws.com/<pool-id>` (no scheme). |
| `user_pool_client_id` | SPA client ID. |
| `hosted_ui_domain` | Fully qualified Hosted UI domain. |
| `hosted_ui_domain_prefix` | Just the prefix portion. |
| `jwt_issuer_url` | `https://<endpoint>`; feeds the API Gateway JWT authorizer. |
| `supported_identity_providers` | List enabled on the SPA client. |

## Identity providers

`supported_identity_providers` is currently `["COGNITO"]` only — the Hosted
UI shows email/password sign-in. The Google and Slack `aws_cognito_identity_provider`
resources were removed because their redirect flows broke in practice;
GitHub-as-OIDC was never enabled (Cognito's generic OIDC provider rejects
the GitHub username mapping — no `sub` claim). Reintroducing them is a
Phase 3.5 follow-up — see the comment block at the top of `main.tf` for
the per-IdP context, including the previously-validated attribute mappings
preserved in the git history.

## Custom attributes

The pool declares one custom attribute used by Shape B persona resolution
(`CognitoPersonaResolver(mode='claim-bound')`):

| Schema name | Type | Mutable | Required | Use |
|---|---|---|---|---|
| `custom:persona` | String (1-64) | yes | no | Shape B resolution per ADR-007 §2-§3. Unused for the public demo (which is Shape A). |

The brief's public demo runs Shape A (request-param) — `custom:persona`
is provisioned but not populated by any IdP. Future client deployments
populate it via Cognito admin APIs or per-IdP attribute mapping.

## Hosted UI domain

The module uses the Cognito-managed domain space
(`*.auth.<region>.amazoncognito.com`). A custom domain at
`auth.ms3dm.tech` is a Phase 3.5 follow-up — see
[phase-3a-brief §7.3](../../../docs/phase-3a-brief.md#73-hosted-ui-domain).
