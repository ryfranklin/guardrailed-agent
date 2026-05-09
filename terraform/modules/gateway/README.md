# gateway

API Gateway HTTP API + JWT authorizer + WAF v2 web ACL + the gateway
Lambda for the Phase 3.a public web demo. Receives `POST /ask` from the
SPA, authenticates against the Cognito user pool, and proxies to the
gateway Lambda which invokes the Bedrock Agent through `gagent_client`.

Source-of-truth specs:
[phase-3a-brief §10](../../../docs/phase-3a-brief.md#10-gateway-terraform-spec-terraformmodulesgateway),
[ADR-010](../../../../consulting/guardrailed-agent/decisions/010-gateway-architecture.md).

## Resource layout

- `main.tf` — HTTP API, JWT authorizer, integration, route, stage, access log group.
- `lambda.tf` — Lambda staging build, IAM role + policy, log group, Lambda function, API GW invoke permission.
- `waf.tf` — WAF v2 web ACL (3 rules) and stage association.
- `variables.tf` / `outputs.tf` / `README.md` — interface.

## Inputs

### Wiring (env layer threads these from sibling modules)

| Variable | Source |
|---|---|
| `cognito_user_pool_arn` | `module.auth.user_pool_arn` |
| `cognito_user_pool_endpoint` | `module.auth.user_pool_endpoint` (no scheme) |
| `cognito_user_pool_client_id` | `module.auth.user_pool_client_id` |
| `agent_id` | `module.agent.agent_id` |
| `agent_alias_id` | `module.agent.agent_alias_id` |
| `agent_alias_arn` | `module.agent.agent_alias_arn` |
| `persona_role_arns` | `{ dispatcher = module.identity.dispatcher_role_arn, technician_lead = module.identity.technician_lead_role_arn, owner = module.identity.owner_role_arn }` |
| `invocation_log_group` | `module.observability.invocation_log_group` |
| `invocation_log_group_arn` | `module.observability.invocation_log_group_arn` |

The Lambda's execution role ARN (`output.lambda_role_arn`) must be added
to `module.identity.trusted_assumer_arns` so each persona role's trust
policy admits this Lambda. The env layer composes that list — see §5.7.

### Lambda packaging

| Variable | Required | Notes |
|---|---|---|
| `lambda_source_dir` | yes | Absolute path to `lambdas/gateway/`. |
| `gagent_client_source_dir` | yes | Absolute path to `gagent_client/`. The package is staged into the Lambda zip alongside `handler.py` so `import gagent_client` resolves at runtime. |
| `lambda_runtime` | no | Default `python3.12`. |
| `lambda_timeout` | no | Default 60s (matches §10). |
| `lambda_memory` | no | Default 1024 MB (matches §10). |
| `log_retention_days` | no | Default 30. Applies to the Lambda log group AND the API GW access log group. |

Build pipeline: `terraform_data.lambda_build` materialises the zip
sources at `${path.module}/.build/${var.env}/src/` by copying the
gateway source plus the `gagent_client` package, then `archive_file`
zips it. Build hashes feed `triggers_replace` so changes to either tree
re-stage and re-zip.

### Behavior

| Variable | Default | Notes |
|---|---|---|
| `env`, `name_prefix`, `tags` | — | Naming + tagging. |
| `persona_resolution_mode` | `request-param` | Sets `GAGENT_GATEWAY_PERSONA_RESOLUTION` on the Lambda. Validated to `request-param` or `claim-bound`. |
| `default_service_region` | `null` | Sets `GAGENT_DEFAULT_SERVICE_REGION` only when non-null. claim-bound + technician_lead claim falls back to this if the body omits `service_region`. |
| `cors_allowed_origins` | `[https://demo.ms3dm.tech, http://localhost:5173]` | Used by both the HTTP API CORS config (preflight) and the Lambda's response Origin echo. |
| `rate_limit_per_5min` | 100 | WAF rate-based rule limit per source IP, 5-minute window. |
| `log_level` | `INFO` | Lambda runtime log level. |

## Outputs

| Output | Notes |
|---|---|
| `api_id`, `api_endpoint`, `api_arn` | HTTP API identifiers. |
| `stage_invoke_url`, `stage_arn` | `$default` stage URL and ARN. |
| `lambda_function_name`, `lambda_arn`, `lambda_role_arn` | Lambda identifiers. `lambda_role_arn` feeds `identity.trusted_assumer_arns`. |
| `web_acl_arn` | WAF v2 ACL ARN. |
| `access_log_group_name`, `lambda_log_group_name` | CloudWatch log groups. |

## IAM

The Lambda execution role carries five statements:

1. **AssumePersonaRoles** — `sts:AssumeRole` + `sts:TagSession` on the three persona role ARNs (with session-tag propagation).
2. **InvokeAgent** — `bedrock:InvokeAgent` on the agent alias ARN only.
3. **EmitInvocationTrace** — `logs:CreateLogStream`/`PutLogEvents`/`DescribeLogStreams` on `/gagent/invocations`.
4. **LambdaOwnLogs** — `logs:CreateLogGroup`/`CreateLogStream`/`PutLogEvents` on the Lambda's runtime log group AND the API GW access log group.
5. **XRay** — `xray:PutTraceSegments` + `xray:PutTelemetryRecords` (resource: `*`, the only level X-Ray supports).

The Lambda's trust policy admits `lambda.amazonaws.com` only.

## CORS

Configured at the HTTP API level so API Gateway handles `OPTIONS /ask`
preflight without invoking the Lambda. Defaults:

- `allow_origins` = `cors_allowed_origins` variable (demo + Vite dev).
- `allow_methods` = `POST, OPTIONS`.
- `allow_headers` = `authorization, content-type, x-gagent-surface`.
- `allow_credentials` = `true`.
- `max_age` = 600s.

The Lambda also echoes `Access-Control-Allow-Origin` on actual responses
when the request `Origin` matches; this redundancy keeps CORS correct
when responses are produced by the Lambda directly.

## WAF

Three rules, REGIONAL scope, default action `Allow`:

| Priority | Rule | Type | Action |
|---|---|---|---|
| 10 | `AWS-AWSManagedRulesCommonRuleSet` | Managed (AWS) | `none{}` (rule defaults) |
| 20 | `AWS-AWSManagedRulesKnownBadInputsRuleSet` | Managed (AWS) | `none{}` (rule defaults) |
| 30 | `RateLimitPerIp` | Rate-based, `IP` aggregate, 5-min window | `block{}` |

Each rule and the ACL itself emit CloudWatch metrics + sampled requests.

**The Web ACL is provisioned but not enforcing.** AWS WAFv2 does not
support API Gateway HTTP API (v2) stages — the rules above are visible in
the WAF console and emit metrics, but no inbound request is evaluated
against them. The deferral and the three unblocking options (AWS adds
support, migrate to REST API, or front with CloudFront) are tracked in
[docs/adr-013-waf-association-deferral.md](../../../docs/adr-013-waf-association-deferral.md).
See also the comment block in `waf.tf`.

## X-Ray

Lambda tracing is set to `Active`. API Gateway HTTP API does not
provide stage-level X-Ray (REST API only), so request-level traces start
at the Lambda. Acceptable for v1; Phase 3.5 may revisit if end-to-end
visibility is needed.

## Telemetry

The Lambda emits structured JSON to `/gagent/invocations` via the shared
`gagent_client.emit_invocation_log()` (one log line per call, surface
tag `web` or `slack`). The same log group serves the gateway Lambda, the
governed_query Lambda's traces (via `gagent_client.invoke`), and any
other surface — one query, all surfaces.

## Notes for §5.7 (env layer wiring)

- Add `module.gateway.lambda_role_arn` to `local.trusted_assumer_arns`
  in `terraform/envs/demo/main.tf` so the persona role trust policies
  admit this Lambda.
- Pass `cors_allowed_origins` only if you need to override (defaults
  cover `demo.ms3dm.tech` and the Vite dev origin).
- For client deployments, set `persona_resolution_mode = "claim-bound"`
  and supply `default_service_region` if the user pool issues
  `custom:persona = technician_lead` claims.
