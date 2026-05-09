# gateway Lambda

Public-demo front door behind API Gateway HTTP API. Receives `POST /ask`
requests already authenticated by API Gateway's JWT authorizer (Cognito
ID token validated upstream), resolves the caller's persona, and invokes
the Bedrock Agent through the shared `gagent_client.invoke()` pipeline.

Source-of-truth specs:
[phase-3a-brief §9](../../docs/phase-3a-brief.md#9-gateway-lambda-spec-lambdasgatewayhandlerpy),
[ADR-007](../../../consulting/guardrailed-agent/decisions/007-multi-user-identity-federation.md),
[ADR-010](../../../consulting/guardrailed-agent/decisions/010-gateway-architecture.md).

## Entry point

```python
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]: ...
```

The handler is wired by the gateway Terraform module (§5.4) as
`handler.handler` and packaged via `archive_file`. The shared
`gagent_client` package is bundled into the same zip.

## Request shape

```http
POST /ask
Authorization: Bearer <Cognito ID token>
Content-Type: application/json

{ "question": "...", "persona": "owner", "service_region": null }
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `question` | string | yes | Non-empty. |
| `persona` | string | depends on mode | Required in `request-param` mode; optional in `claim-bound` mode (must agree with claim if present). |
| `service_region` | string | technician_lead only | Required in `request-param` mode for `technician_lead`. In `claim-bound` mode, falls back to `GAGENT_DEFAULT_SERVICE_REGION` if absent. |

Headers honored:

| Header | Purpose |
|---|---|
| `Authorization: Bearer <jwt>` | Validated by API Gateway authorizer; claims arrive in `event.requestContext.authorizer.jwt.claims`. |
| `X-Gagent-Surface` | Optional; allowlist `{web, slack}`. Defaults to `web`. Set to `slack` by the Phase 3.b adapter when invoking via Lambda Invoke. |
| `Origin` | Echoed back in `Access-Control-Allow-Origin` if it matches `GAGENT_GATEWAY_ALLOWED_ORIGINS` (default: `https://demo.ms3dm.tech`, `http://localhost:5173`). |

## Response shapes

Success (200):

```json
{
  "text": "...",
  "persona": "owner",
  "service_region": null,
  "tools_called": ["/customers"],
  "guardrail_blocks": 0,
  "duration_seconds": 4.821,
  "session_id": "gagent-owner-abc123"
}
```

Error (any non-2xx):

```json
{ "error": "<short_code>", "message": "<detail>" }
```

| Status | `error` | When |
|---|---|---|
| 400 | `invalid_body` | Body missing, not JSON, wrong types. |
| 400 | `invalid_persona` | Shape A persona missing/invalid; technician_lead missing service_region; claim-bound resolution failed (claim absent or invalid). |
| 400 | `invalid_surface` | `X-Gagent-Surface` header outside the allowlist. |
| 401 | `unauthorized` | JWT claims absent (defensive — the authorizer should reject earlier). |
| 403 | `persona_mismatch` | claim-bound mode and request body's persona disagrees with the claim. |
| 429 | `throttled` | Bedrock throttled. |
| 500 | `internal_error` / `upstream_error` | Unhandled exception or AWS client error. |
| 504 | `upstream_timeout` | Bedrock InvokeAgent read/connect timeout. |

CORS: success and error responses include `Vary: Origin` and (when the
caller's `Origin` is allowlisted) `Access-Control-Allow-Origin` echoed
back. API Gateway handles `OPTIONS /ask` preflight; the Lambda is not
invoked for preflight.

## Environment variables

Required:

| Var | Purpose |
|---|---|
| `GAGENT_AGENT_ID` | Bedrock Agent ID (from `agent` module output). |
| `GAGENT_AGENT_ALIAS_ID` | Bedrock Agent alias ID. |
| `AWS_REGION` | Lambda runtime sets this; reused for InvokeAgent. |
| `GAGENT_DISPATCHER_ROLE_ARN` | Dispatcher persona role ARN (from `identity` module). |
| `GAGENT_TECHNICIAN_LEAD_ROLE_ARN` | TechnicianLead persona role ARN. |
| `GAGENT_OWNER_ROLE_ARN` | Owner persona role ARN. |

Optional:

| Var | Default | Purpose |
|---|---|---|
| `GAGENT_GATEWAY_PERSONA_RESOLUTION` | `request-param` | `request-param` (Shape A) or `claim-bound` (Shape B). |
| `GAGENT_DEFAULT_SERVICE_REGION` | unset | Used only by `claim-bound` mode when a `technician_lead` claim arrives without an explicit `service_region`. |
| `GAGENT_LOG_GROUP` | `/gagent/invocations` | CloudWatch log group for invocation traces. |
| `GAGENT_GATEWAY_ALLOWED_ORIGINS` | demo + Vite dev | Comma-separated CORS origin allowlist. |
| `LOG_LEVEL` | `INFO` | Lambda log level. |

## Persona resolution

Routes through `gagent_client.identity.CognitoPersonaResolver` (§5.2).
Mode is fixed at construction from `GAGENT_GATEWAY_PERSONA_RESOLUTION`:

- **request-param** (public demo): the resolver ignores the JWT's
  `custom:persona` claim and uses the request body's `persona` /
  `service_region`. Each row of §8's behavior table maps to a defined
  HTTP status — see the error table above.
- **claim-bound** (future client deployments): the resolver requires
  `claims.custom:persona` and rejects body-supplied personas that
  disagree (`PermissionError` → 403). technician_lead falls back to
  `GAGENT_DEFAULT_SERVICE_REGION` when the body omits `service_region`.

JWT validation lives in the API Gateway JWT authorizer (configured in
§5.4); the Lambda trusts the decoded claims dict.

## Surface tagging

Surface defaults to `web`. The gateway accepts `X-Gagent-Surface: slack`
from the Phase 3.b Slack adapter, which calls this Lambda directly via
Lambda Invoke. The same `/gagent/invocations` log group serves both
surfaces; `surface` is a structured field on each log line so dashboards
can split web vs Slack traffic.

## Telemetry

`gagent_client.invoke()` emits a structured JSON line to
`/gagent/invocations` per call. The Lambda does not emit additional
trace events — the shared library is the single emitter so the eval
harness, MCP server, and gateway all produce identically-shaped log
lines (ADR-006).

## How the gateway Terraform module wires this up (§5.4)

- Packages `lambdas/gateway/` plus the bundled `gagent_client` library
  via `archive_file`, the same pattern used for `governed_query`.
- IAM role allows `sts:AssumeRole` on the three persona role ARNs (with
  session-tag transitive propagation), `bedrock:InvokeAgent` on the
  agent alias ARN, `logs:PutLogEvents` to `/gagent/invocations`, and
  `xray:PutTraceSegments`.
- API Gateway HTTP API integration: Lambda proxy on `POST /ask`, JWT
  authorizer pointed at the Cognito user pool issuer (from §5.1's
  `jwt_issuer_url`).
- WAF v2 web ACL attached to the API stage with the three rule groups
  per ADR-010 §5.

## Tests

`pytest lambdas/gateway/tests/test_handler.py` exercises every branch
in `handler()` with mocked `gagent_client.invoke`. No live AWS calls.
Run from the repo root so the package import path resolves correctly.
