
# Phase 3.a Implementation Brief — Public web demo

**Audience:** Claude Code, picking up a fresh session inside the existing
public `guardrailed-agent` repo. This brief is the source of truth for the
Phase 3.a build — the public demo URL at `demo.ms3dm.tech`.

**Your job, in priority order:**

1. Implement §5's eight components in dependency order.
2. Use the existing repo conventions (kebab-case, no emojis, no comments
   unless *why* is non-obvious, area-prefixed commits — `tf:`, `lambda:`,
   `web:`, `auth:`, `gateway:`, `docs:`).
3. Stop the moment §6's acceptance criteria are met. Do not extend scope.
4. Defer §16's items explicitly to Phase 3.5 — do not pre-build them.

This brief is **self-contained**. It assumes the Phase 1 + Phase 2 + ADR-008
HVAC pivot are already shipped (they are, per
[[../../consulting/guardrailed-agent/decisions/README|the decision log]]).

---

## 1. Mission

Ship `demo.ms3dm.tech` as an auth-gated public demo URL where a stranger
with a Cognito sign-in can pick a persona per session and feel the Lake
Formation gate live. Same Bedrock Agent that the Phase 1 smoke test
exercises, now reachable from any browser.

**The acceptance gate** (full version in §6):

> *A new visitor can land on `demo.ms3dm.tech`, sign in with Google (or
> any of the four IdPs), pick `Owner`, ask "Show me customer 32869c51's
> contact info," see realistic synthetic PII, switch to `Dispatcher`,
> ask the same question, see redacted output. Both invocations land in
> CloudWatch Logs Insights and CloudTrail under the same Cognito user.*

That moment is what Phase 3.a ships.

The selling sentence reinforces the architecture's claim:

> *"The demo URL is reviewable IaC, auditable in CloudTrail, governed by
> Lake Formation, and inside a single AWS perimeter. The front-end is on
> CloudFront. The auth is in Cognito. There is no Vercel, no Auth0, no
> Cloudflare in the trust chain."*

---

## 2. Decisions already made (do not redebate)

These are non-negotiable. Source ADRs live at
`consulting/guardrailed-agent/decisions/`.

| Decision | Choice | Source |
|---|---|---|
| **Identity provider** | Amazon Cognito User Pool with four federated IdPs: email/password (native), Google OIDC, GitHub OIDC, Slack OIDC | [ADR-007](decisions/007-multi-user-identity-federation.md) |
| **Persona resolution** | Shape A (request-param) for the public demo; Shape B (claim-bound) for future client deployments. Mode selected by `GAGENT_GATEWAY_PERSONA_RESOLUTION` env var. | [ADR-007 §2-§3](decisions/007-multi-user-identity-federation.md) |
| **API surface** | API Gateway HTTP API with built-in JWT authorizer pointed at the Cognito User Pool | [ADR-010](decisions/010-gateway-architecture.md) |
| **Routes** | Single route `POST /ask`. CORS preflight for `OPTIONS /ask`. | [ADR-010 §2](decisions/010-gateway-architecture.md) |
| **Response shape** | Buffered (no streaming v1). Lambda proxy integration. JSON envelope per ADR-010 §2. | [ADR-010 §3](decisions/010-gateway-architecture.md) |
| **WAF** | AWS WAF v2 attached to the HTTP API stage. AWS managed common rules + known-bad-inputs + IP-based rate limit (100 req / 5 min). | [ADR-010 §5](decisions/010-gateway-architecture.md) |
| **Front-end stack** | Vite + React 18 + TypeScript SPA. Tailwind CSS for styling. AWS Amplify Auth library (not Amplify framework). Cognito Hosted UI for sign-in. | [ADR-012](decisions/012-web-demo.md) |
| **Front-end hosting** | S3 + CloudFront with ACM cert in `us-east-1`. No Vercel, no Amplify Hosting. | [ADR-012 §2](decisions/012-web-demo.md) |
| **Persona-picker UX** | Modal at session start; header indicator with [change] button that resets the conversation | [ADR-012 §5](decisions/012-web-demo.md) |
| **State** | Chat history in React state (in-memory). Session ID in `sessionStorage`. JWT/refresh handled by Amplify Auth. | [ADR-012 §7](decisions/012-web-demo.md) |
| **Domain** | `demo.ms3dm.tech` via CNAME at IONOS pointing to the CloudFront distribution. ACM cert validated via temporary DNS record at IONOS. | [ADR-012 §6](decisions/012-web-demo.md) |
| **Telemetry** | Same `/gagent/invocations` CloudWatch log group via `gagent_client.emit_invocation_log()`. X-Ray on the HTTP API stage and the gateway Lambda. | [ADR-010 §8](decisions/010-gateway-architecture.md) |
| **Streaming** | Deferred to Phase 3.5. v1 buffers the full agent response and returns one JSON object. | [ADR-010 §3](decisions/010-gateway-architecture.md) |
| **Slack adapter** | Phase 3.b. Out of scope here. | [ADR-011 (TBD)](decisions/) |

---

## 3. Tech stack constraints

| Concern | Choice | Note |
|---|---|---|
| **Lambda runtime** | Python 3.12 | Same as `governed_query` Lambda. Reuses `gagent_client`. |
| **Web app language** | TypeScript (strict mode) | No `any` without justification. |
| **Web framework** | React 18 | Stable. No Server Components needed (SPA). |
| **Web bundler** | Vite (latest stable) | Fast HMR; static-file output. |
| **Styling** | Tailwind CSS | Default config; no design tokens upfront. |
| **Auth client** | `@aws-amplify/auth` (the library, not the full Amplify framework) | Tree-shaken to keep bundle small. |
| **HTTP client** | Native `fetch` with a thin wrapper | No axios; no react-query (chat is one route). |
| **Routing** | `react-router-dom` v6 | Two routes: `/` (chat, auth-gated) and `/auth/callback` (Cognito redirect handler). |
| **Terraform** | `>= 1.7` | AWS provider `~> 5.0`. |
| **AWS region** | `us-east-1` | Required for CloudFront ACM certs. |
| **Node** | 20.x LTS | For local dev and CI. |
| **Package manager** | `pnpm` (preferred) or `npm` | Either works; pin `packageManager` in package.json. |

---

## 4. Repo additions (target structure)

These are the new and modified paths. Everything else in the repo stays
unchanged.

```
guardrailed-agent/
├── lambdas/
│   ├── governed_query/                 (existing — unchanged)
│   └── gateway/                        NEW
│       ├── handler.py
│       ├── requirements.txt
│       ├── README.md
│       └── tests/
│           ├── __init__.py
│           └── test_handler.py
│
├── web/                                NEW (entire directory)
│   ├── package.json
│   ├── pnpm-lock.yaml (or package-lock.json)
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── postcss.config.cjs
│   ├── index.html
│   ├── .env.example
│   ├── README.md
│   ├── public/
│   │   ├── favicon.ico
│   │   └── og.png
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── routes.tsx
│       ├── auth/
│       │   ├── amplifyConfig.ts
│       │   ├── AuthGate.tsx
│       │   └── AuthCallback.tsx
│       ├── components/
│       │   ├── PersonaModal.tsx
│       │   ├── PersonaIndicator.tsx
│       │   ├── ChatView.tsx
│       │   ├── MessageList.tsx
│       │   ├── ComposerInput.tsx
│       │   └── ErrorBanner.tsx
│       ├── api/
│       │   ├── client.ts                (POST /ask wrapper)
│       │   └── types.ts
│       ├── state/
│       │   ├── persona.ts
│       │   └── session.ts
│       └── styles/
│           └── globals.css
│
├── terraform/
│   ├── modules/
│   │   ├── (existing modules unchanged)
│   │   ├── auth/                       NEW
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   ├── outputs.tf
│   │   │   └── README.md
│   │   ├── gateway/                    NEW
│   │   │   ├── main.tf
│   │   │   ├── waf.tf
│   │   │   ├── lambda.tf
│   │   │   ├── variables.tf
│   │   │   ├── outputs.tf
│   │   │   └── README.md
│   │   └── web-demo/                   NEW
│   │       ├── main.tf
│   │       ├── cloudfront.tf
│   │       ├── variables.tf
│   │       ├── outputs.tf
│   │       └── README.md
│   └── envs/
│       └── demo/
│           └── main.tf                 UPDATED (composes the 3 new modules)
│
├── gagent_client/
│   └── identity.py                     UPDATED (CognitoPersonaResolver added)
│
├── scripts/
│   └── smoke-web.sh                    NEW
│
├── docs/
│   ├── phase-3a-brief.md               NEW (this file, copied from vault)
│   └── domains-and-dns.md              NEW
│
└── .github/
    └── workflows/
        ├── terraform.yml               UPDATED (plan the new modules)
        ├── eval.yml                    (existing — unchanged)
        └── web.yml                     NEW (typecheck + build + deploy)
```

---

## 5. Dependency-ordered work breakdown

Implement in this order. Each step has a clear input → output contract.
Stop at the boundary; do not interleave.

### 5.1 — `terraform/modules/auth/`

**Input:** the env `demo` exists; ACM-cert region is `us-east-1`; the
Cognito Hosted UI domain prefix is `gagent-demo` (or `gagent-${var.env}`).

**Output:**
- `aws_cognito_user_pool` with email + password native sign-in
- `aws_cognito_user_pool_client` with OAuth scopes (`email`, `openid`,
  `profile`) and authorization code flow
- Three `aws_cognito_identity_provider` resources for Google, GitHub,
  Slack (provider type `OIDC` for all three; configured per §7)
- `aws_cognito_user_pool_domain` for the Hosted UI
- Outputs: `user_pool_id`, `user_pool_client_id`, `user_pool_arn`,
  `user_pool_endpoint`, `hosted_ui_domain`, `jwt_issuer_url`

**Key detail:** Slack's OIDC provider is *not* a first-class Cognito
provider type — use Cognito's generic OIDC provider with Slack's
authorization, token, userinfo, and JWKS URLs. Document the OIDC
attribute mapping in the module README so callers know what
custom-attribute claims they get.

### 5.2 — `gagent_client.identity.CognitoPersonaResolver`

**Input:** the existing `Persona`, `PersonaResolver` Protocol,
`FlagPersonaResolver`, and `SsoPersonaResolver` in
`gagent_client/identity.py`.

**Output:** a third resolver class `CognitoPersonaResolver` with the
contract in §8.

**Key detail:** the resolver does NOT validate JWTs (the API Gateway
authorizer does that upstream). It accepts an already-decoded claims
dict + an optional request-body persona. The mode (Shape A vs Shape B)
is selected at construction time via the `mode` parameter, with the
default coming from the `GAGENT_GATEWAY_PERSONA_RESOLUTION` env var.

### 5.3 — `lambdas/gateway/`

**Input:** §5.1 outputs (Cognito user pool ID, JWT issuer); §5.2's
`CognitoPersonaResolver`; the existing `gagent_client.invoke()`.

**Output:** the gateway Lambda per §9, with handler entry point
`handler.handler`. Tests in `lambdas/gateway/tests/test_handler.py`.

**Key detail:** the Lambda uses `gagent_client.invoke(question,
persona, surface=<surface>, ...)`. Surface defaults to `"web"`; an
`X-Gagent-Surface` header (validated against an allowlist of `web`,
`slack`) overrides it. This sets up Phase 3.b's Slack adapter to call
the gateway Lambda directly via Lambda Invoke.

### 5.4 — `terraform/modules/gateway/`

**Input:** §5.1's `auth/` outputs; §5.3's Lambda zip artifact (built
the same way `terraform/modules/tools/` builds the `governed_query`
Lambda — `archive_file`).

**Output:** per §10. API Gateway HTTP API + JWT authorizer + WAF + the
gateway Lambda + IAM role with `sts:AssumeRole` on the persona ARNs +
`logs:PutLogEvents` to `/gagent/invocations` + `bedrock:InvokeAgent`
on the agent alias ARN.

### 5.5 — `web/` SPA

**Input:** §5.1 outputs (user pool ID, client ID, hosted UI domain);
§5.4 outputs (API endpoint URL); the persona enums (hardcoded in TS).

**Output:** per §11. A built static bundle at `web/dist/` after `pnpm
build`. Unit tests in `web/src/**/*.test.tsx` (Vitest).

**Key detail:** build-time env vars come from a `.env` file populated
at build time by the CI workflow (§5.7) using `terraform output`. For
local dev, use a `.env.local` file the developer populates manually
from `terraform output`.

### 5.6 — `terraform/modules/web-demo/`

**Input:** §5.5's static bundle path (`web/dist/`).

**Output:** per §12. S3 bucket + CloudFront distribution + ACM cert +
OAC + bucket policy. Outputs: `distribution_domain_name`,
`distribution_id`, `bucket_name`, `bucket_arn`.

**Key detail:** the ACM cert validation requires a DNS record at
IONOS (per §13). The Terraform module emits the validation record
parameters as outputs; the operator adds them to IONOS manually (or
via IONOS API if scripted later). Module waits on `aws_acm_certificate_validation`.

### 5.7 — Compose in `terraform/envs/demo/main.tf` + new CI workflow

**Input:** all six new modules + the existing env composition.

**Output:** the env layer instantiates `auth`, `gateway`, and `web-demo`
modules, threading outputs as needed (auth.user_pool_id →
gateway.cognito_user_pool_id; gateway.api_endpoint → web build env vars).
A new `.github/workflows/web.yml` workflow runs typecheck + build + S3
sync + CloudFront cache invalidation on push to `main` when files under
`web/**` change. Updates to `.github/workflows/terraform.yml` so it
plans the new modules.

### 5.8 — DNS at IONOS

**Input:** §5.6 output `distribution_domain_name` + ACM validation
parameters.

**Output:** two DNS records added at IONOS:
- `_acme-challenge.demo.ms3dm.tech` CNAME to ACM-supplied target
  (temporary; remove after cert issued)
- `demo.ms3dm.tech` CNAME to `<distribution_domain_name>` (permanent)

Documented in `docs/domains-and-dns.md`.

---

## 6. Phase 3.a acceptance criteria

Phase 3.a ships when **all** of the following are true. Do not declare
done early.

1. **Terraform plans cleanly** in `terraform/envs/demo/` with the three
   new modules composed; `terraform apply` succeeds end-to-end from a
   clean state.
2. **Cognito User Pool exists** with all four IdPs configured (Email,
   Google, GitHub, Slack). Hosted UI loads at the configured domain.
3. **`CognitoPersonaResolver`** is in `gagent_client/identity.py`,
   exported from `__init__.py`, with unit tests covering Shape A and
   Shape B modes, valid + invalid persona inputs, technician_lead
   service_region requirement.
4. **Gateway Lambda** is provisioned, responds to `POST /ask` under a
   valid Cognito JWT, and successfully invokes Bedrock Agent under each
   of the three persona ARNs. `surface="web"` lands in `/gagent/invocations`.
5. **API Gateway HTTP API** is reachable at its `execute-api` URL with
   the JWT authorizer rejecting unauthenticated requests with 401 and
   accepting valid Cognito JWTs.
6. **WAF is attached** to the HTTP API stage with the three configured
   rule groups. Rate-rule trips at the configured threshold (verifiable
   by hitting the endpoint > 100 times in 5 minutes from one IP and
   getting 429s).
7. **Web SPA builds cleanly** (`pnpm build`), passes type-check
   (`pnpm typecheck`), and renders the chat view + persona modal in
   local dev (`pnpm dev`).
8. **CloudFront distribution** serves the web bundle behind the ACM cert
   for `demo.ms3dm.tech`. Visiting `demo.ms3dm.tech` shows the SPA shell.
9. **DNS resolves** `demo.ms3dm.tech` to the CloudFront distribution.
10. **Sign-in flow works** end-to-end: visit `demo.ms3dm.tech` → click
    "Sign in" → redirect to Cognito Hosted UI → pick an IdP → return to
    the SPA with a valid JWT.
11. **Persona picker works**: post-sign-in modal blocks the chat until
    a persona is picked; header indicator shows the active persona;
    [change] reopens the modal and clears the conversation.
12. **End-to-end smoke test passes** (`scripts/smoke-web.sh`): same
    prompt under all three personas returns visibly different content;
    Dispatcher response contains redacted markers; Owner response
    contains synthetic-but-realistic PII; sensitivity-tagged columns
    are masked for Dispatcher and TechnicianLead. All three invocations
    appear in `/gagent/invocations` and CloudTrail.

When 1-12 hold, ship the launch post (Substack #11 — see the strategy
doc queue).

---

## 7. Cognito user pool config (the `auth/` module)

### 7.1 User Pool

- Name: `${var.name_prefix}${var.env}` (e.g., `gagent-demo`)
- Username attributes: `email`
- Auto-verified attributes: `email`
- Password policy: minimum 12 chars, require uppercase + lowercase +
  digits; no symbol requirement (UX trade-off)
- MFA: optional in v1 (TOTP)
- Account recovery: email
- Schema:
  - Built-in `email` (required)
  - Built-in `name` (optional)
  - Custom attribute `custom:persona` (string, optional, mutable) —
    used for Shape B in future client deployments; not used for the
    public demo

### 7.2 User Pool Client

- Name: `${var.name_prefix}${var.env}-web`
- Generate secret: **false** (SPAs cannot keep secrets)
- Allowed OAuth flows: authorization code grant
- Allowed OAuth scopes: `email`, `openid`, `profile`
- Allowed callback URLs:
  - `https://demo.ms3dm.tech/auth/callback`
  - `http://localhost:5173/auth/callback` (Vite dev server)
- Allowed logout URLs:
  - `https://demo.ms3dm.tech/`
  - `http://localhost:5173/`
- Supported identity providers: `COGNITO`, `Google`, `GitHub`, `Slack`
- Token validity: ID token 1h, Access token 1h, Refresh token 30 days
- Prevent user existence errors: enabled

### 7.3 Hosted UI domain

- `aws_cognito_user_pool_domain` with prefix `gagent-${var.env}` (or
  configurable via variable)
- Cognito-managed `*.auth.us-east-1.amazoncognito.com` subdomain in v1
- Custom domain (`auth.ms3dm.tech`) is Phase 3.5 — defer

### 7.4 Identity Providers

Each IdP requires the operator to set up an OAuth app on the IdP side
and pass the credentials in via Terraform variables (sensitive). The
module never hardcodes them.

#### Google OIDC

```hcl
resource "aws_cognito_identity_provider" "google" {
  user_pool_id  = aws_cognito_user_pool.this.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    client_id        = var.google_client_id
    client_secret    = var.google_client_secret
    authorize_scopes = "openid email profile"
  }

  attribute_mapping = {
    email    = "email"
    username = "sub"
  }
}
```

#### GitHub OIDC (generic OIDC provider)

GitHub does not support OIDC natively for app authentication; it uses
OAuth 2.0 with a custom user-info endpoint. Cognito's generic OIDC
provider can wrap it. Use these endpoints:

- Authorize: `https://github.com/login/oauth/authorize`
- Token: `https://github.com/login/oauth/access_token`
- User-info: `https://api.github.com/user`
- JWKS: not applicable; use `attributes_request_method = "GET"` and
  manually map the user-info response

Document this clearly — GitHub-as-OIDC is the trickiest of the four. If
it becomes painful, simplify to Google + email/password for v1 launch and
add GitHub in Phase 3.5.

#### Slack OIDC

Slack does support OpenID Connect for "Sign in with Slack." Use:

- Authorize: `https://slack.com/openid/connect/authorize`
- Token: `https://slack.com/api/openid.connect.token`
- User-info: `https://slack.com/api/openid.connect.userInfo`
- JWKS: `https://slack.com/openid/connect/keys`

Cognito's generic OIDC provider type works.

### 7.5 Variables (sensitive)

- `google_client_id`, `google_client_secret`
- `github_client_id`, `github_client_secret`
- `slack_client_id`, `slack_client_secret`

Pass via `terraform.tfvars` (gitignored) or environment variables
(`TF_VAR_*`). Never commit.

---

## 8. `CognitoPersonaResolver` spec

New class in `gagent_client/identity.py`. Third resolver alongside
`FlagPersonaResolver` (Shape A, local) and `SsoPersonaResolver` (Shape B,
IIC).

```python
class CognitoPersonaResolver:
    """Persona resolution for Cognito-authenticated callers.

    Two modes:
    - Shape A (mode='request-param'): persona is supplied per-call
      from the request body. JWT only authenticates. Used for the
      public demo where synthetic data makes per-session persona
      selection safe.
    - Shape B (mode='claim-bound'): persona is taken from JWT claims
      (custom:persona). Body-supplied persona that disagrees with
      claim is rejected.
    """

    VALID_MODES = ("request-param", "claim-bound")

    def __init__(
        self,
        role_arns: Mapping[str, str],
        *,
        mode: str | None = None,
        env: Mapping[str, str] | None = None,
    ):
        ...

    def resolve(
        self,
        *,
        claims: dict[str, Any],
        requested_role: str | None = None,
        requested_service_region: str | None = None,
    ) -> Persona:
        """Return a Persona object given Cognito claims + request hints.

        Shape A:
          - If requested_role is None, raise ValueError.
          - If requested_role is technician_lead and
            requested_service_region is None, raise ValueError.
          - Else construct Persona(role=requested_role, ...).

        Shape B:
          - Read claims['custom:persona']. If absent, raise SsoMappingError.
          - If requested_role differs from claim, raise PermissionError
            (caller's request body lied about persona).
          - Construct Persona from the claim.
        """
        ...
```

### Behavior table

| Mode | claims.custom:persona | requested_role | service_region | Result |
|---|---|---|---|---|
| request-param | (any) | dispatcher | (any) | Persona(dispatcher) |
| request-param | (any) | technician_lead | tempe-mesa | Persona(technician_lead, region) |
| request-param | (any) | technician_lead | None | ValueError |
| request-param | (any) | None | (any) | ValueError |
| request-param | (any) | invalid | (any) | ValueError |
| claim-bound | dispatcher | None | (any) | Persona(dispatcher) |
| claim-bound | dispatcher | dispatcher | (any) | Persona(dispatcher) |
| claim-bound | dispatcher | owner | (any) | PermissionError |
| claim-bound | (absent) | (any) | (any) | SsoMappingError |

### Tests

`tests/gagent_client/test_identity_cognito.py` covers:
- Shape A happy paths for each persona
- Shape A missing persona raises
- Shape A invalid persona raises
- Shape A technician_lead without service_region raises
- Shape B happy path
- Shape B missing claim raises
- Shape B mismatched body raises
- Mode resolution from env var when not passed explicitly

---

## 9. Gateway Lambda spec (`lambdas/gateway/handler.py`)

### Entry point

```python
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    ...
```

### Request shape (from API Gateway HTTP API + JWT authorizer)

```python
event = {
    "version": "2.0",
    "routeKey": "POST /ask",
    "headers": {...},
    "requestContext": {
        "authorizer": {
            "jwt": {
                "claims": {
                    "sub": "...",
                    "email": "...",
                    "custom:persona": "...",  # may be absent
                    ...
                },
                "scopes": [...],
            },
        },
        ...
    },
    "body": '{"question":"...","persona":"owner","service_region":null}',
    ...
}
```

### Response shape (success)

```python
{
    "statusCode": 200,
    "headers": {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "<allowed origin from header echo>",
    },
    "body": json.dumps({
        "text": "...",
        "persona": "owner",
        "service_region": None,
        "tools_called": ["/customers"],
        "guardrail_blocks": 0,
        "duration_seconds": 4.821,
        "session_id": "gw-owner-abc123",
    }),
}
```

### Response shape (error)

```python
{
    "statusCode": 400 | 403 | 429 | 500 | 504,
    "headers": {...},
    "body": json.dumps({"error": "<short_code>", "message": "<detail>"}),
}
```

### Logic

1. Parse `event["body"]`. Validate shape (`question` required string;
   `persona` optional string; `service_region` optional string). On
   invalid: 400.
2. Resolve persona via `CognitoPersonaResolver.resolve(claims=...,
   requested_role=..., requested_service_region=...)`. On failure (mode
   selected by env var): map errors to 400 (validation) or 403
   (claim mismatch).
3. Determine `surface` — default `"web"`; honor `event["headers"].get
   ("x-gagent-surface")` if present and in allowlist (`{"web","slack"}`).
4. Call `gagent_client.invoke(question, persona, agent_id=..., agent_alias_id=...,
   region=..., surface=surface, ...)`. On `ClientError`: map throttle to
   429, timeout to 504, other to 500.
5. Build the response envelope; emit any token-budget warnings via
   structured log.
6. Return the response. Logging is handled by `gagent_client.emit_invocation_log`.

### Environment variables

- `GAGENT_AGENT_ID`, `GAGENT_AGENT_ALIAS_ID` — from terraform output
- `GAGENT_DISPATCHER_ROLE_ARN`, `GAGENT_TECHNICIAN_LEAD_ROLE_ARN`,
  `GAGENT_OWNER_ROLE_ARN` — from terraform output
- `GAGENT_LOG_GROUP` — `/gagent/invocations`
- `AWS_REGION` — `us-east-1`
- `GAGENT_GATEWAY_PERSONA_RESOLUTION` — `request-param` (Shape A) for the
  demo; flipped to `claim-bound` in client deployments via Terraform var
- `GAGENT_DEFAULT_SERVICE_REGION` — `tempe-mesa` (only used if
  technician_lead persona is requested without explicit service_region;
  optional)

### Tests

Unit tests in `lambdas/gateway/tests/test_handler.py`:
- Valid request / each persona / surface=web → calls invoke(); response
  shaped correctly
- Invalid body → 400
- Missing JWT (shouldn't happen — authorizer rejects upstream — but
  defensive): 401
- Persona resolution failure (mode=claim-bound, mismatched body): 403
- ClientError throttle → 429
- ClientError timeout → 504

Mock `gagent_client.invoke` and `boto3` clients; pure-logic tests run in
the `mcp-unit` CI tier (no AWS).

---

## 10. Gateway Terraform spec (`terraform/modules/gateway/`)

### Resources

- `aws_apigatewayv2_api` — HTTP API; CORS configured (allow origins:
  `https://demo.ms3dm.tech` and `http://localhost:5173`; allow methods:
  `POST`, `OPTIONS`; allow headers: `authorization, content-type,
  x-gagent-surface`)
- `aws_apigatewayv2_authorizer` — JWT authorizer pointed at the
  Cognito User Pool issuer; identity source `$request.header.Authorization`
- `aws_apigatewayv2_route` — `POST /ask` with the JWT authorizer
- `aws_apigatewayv2_integration` — Lambda proxy to the gateway Lambda
- `aws_apigatewayv2_stage` — `$default` stage; X-Ray tracing enabled;
  access logs to a CloudWatch log group with retention set
- `aws_lambda_function` — the gateway Lambda (Python 3.12, 1024 MB,
  timeout 60s; same packaging pattern as `governed_query`)
- `aws_iam_role` — Lambda execution role
- `aws_iam_role_policy` — STS AssumeRole on the three persona ARNs;
  Bedrock InvokeAgent on the agent alias ARN; CloudWatch Logs
  PutLogEvents on `/gagent/invocations` and the Lambda's own log group;
  X-Ray PutTraceSegments
- `aws_lambda_permission` — allow API Gateway to invoke
- `aws_wafv2_web_acl` — Web ACL with three rules:
  - `AWS-AWSManagedRulesCommonRuleSet` (rule group reference)
  - `AWS-AWSManagedRulesKnownBadInputsRuleSet`
  - Custom rate-based rule: 100 requests / 5 min per IP; action `Block`
- `aws_wafv2_web_acl_association` — attach to the API Gateway stage

### Variables

- `env`, `tags`, `name_prefix`
- `lambda_source_dir` — path to `lambdas/gateway/`
- `cognito_user_pool_arn`, `cognito_user_pool_endpoint`,
  `cognito_user_pool_client_id`
- `agent_id`, `agent_alias_id`, `agent_alias_arn`
- `persona_role_arns` — map of `{dispatcher, technician_lead, owner} →
  ARN`
- `invocation_log_group`, `invocation_log_group_arn`
- `persona_resolution_mode` — `request-param` (default for demo) or
  `claim-bound`
- `cors_allowed_origins` — list, defaults to `["https://demo.ms3dm.tech",
  "http://localhost:5173"]`
- `rate_limit_per_5min` — number, defaults to 100

### Outputs

- `api_id`, `api_endpoint`, `api_arn`
- `stage_invoke_url`
- `lambda_function_name`, `lambda_arn`
- `web_acl_arn`

---

## 11. Web SPA spec (`web/`)

### `package.json` (key dependencies)

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "typecheck": "tsc --noEmit",
    "lint": "eslint . --ext .ts,.tsx",
    "test": "vitest run",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.21.0",
    "@aws-amplify/auth": "^6.x"
  },
  "devDependencies": {
    "typescript": "^5.x",
    "vite": "^5.x",
    "@vitejs/plugin-react": "^4.x",
    "tailwindcss": "^3.x",
    "postcss": "...",
    "autoprefixer": "...",
    "eslint": "...",
    "vitest": "...",
    "@testing-library/react": "..."
  }
}
```

### Entry: `src/main.tsx`

- Configure Amplify Auth at module load via `Amplify.configure({...})`
  with values from `import.meta.env.VITE_*`
- Wrap the app in `BrowserRouter` and an `<AuthGate>` component

### Routes (`src/routes.tsx`)

- `/` — `<ChatView>` (auth-gated)
- `/auth/callback` — `<AuthCallback>` (handles Cognito redirect)
- `*` — redirect to `/`

### Components

- `<AuthGate>` — checks Amplify Auth state; if signed out, calls
  `signInWithRedirect()` to Cognito Hosted UI; else renders children
- `<AuthCallback>` — completes the Amplify redirect dance;
  redirects to `/` after success
- `<PersonaModal>` — full-screen modal blocking the chat until a
  persona is selected; three radio options + `[Start chatting]` button
- `<PersonaIndicator>` — header element showing active persona +
  `[change]` button
- `<ChatView>` — wraps `<MessageList>` and `<ComposerInput>`; on persona
  change, clears messages
- `<MessageList>` — renders an array of message objects (`role`,
  `content`, `tools_called`, `duration`)
- `<ComposerInput>` — text area + send button; calls `api/client.ts`
- `<ErrorBanner>` — shown when an `/ask` call fails

### State

- `usePersona()` hook backed by React state (no global store; small app)
- `useSession()` returns a `session_id` from `sessionStorage`,
  generating one on first call per tab
- Messages are component-local React state in `<ChatView>`

### `api/client.ts`

```typescript
export async function postAsk(input: AskRequest): Promise<AskResponse> {
  const session = await fetchAuthSession()
  const token = session.tokens?.idToken?.toString()
  if (!token) throw new Error('Not authenticated')

  const res = await fetch(`${import.meta.env.VITE_API_ENDPOINT}/ask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify(input),
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new ApiError(res.status, err.error ?? 'unknown', err.message ?? '')
  }
  return res.json()
}
```

### Build-time env vars (`.env.example`)

```
VITE_API_ENDPOINT=https://<id>.execute-api.us-east-1.amazonaws.com
VITE_USER_POOL_ID=us-east-1_xxxxx
VITE_USER_POOL_CLIENT_ID=xxxxx
VITE_COGNITO_DOMAIN=gagent-demo.auth.us-east-1.amazoncognito.com
VITE_REDIRECT_SIGN_IN=https://demo.ms3dm.tech/auth/callback
VITE_REDIRECT_SIGN_OUT=https://demo.ms3dm.tech/
VITE_AWS_REGION=us-east-1
```

### Acceptance for the SPA in isolation

- `pnpm install && pnpm typecheck && pnpm build` succeeds
- `pnpm test` passes
- `pnpm dev` shows the sign-in redirect (no real auth needed if
  Cognito values point at a deployed pool)

---

## 12. Web demo Terraform spec (`terraform/modules/web-demo/`)

### Resources

- `aws_s3_bucket` — name `${var.name_prefix}web-${var.env}-${account_id}`;
  versioning enabled; SSE-S3 encryption; block public access (CloudFront-only
  via OAC); ownership BucketOwnerEnforced
- `aws_cloudfront_origin_access_control` — `sigv4` signing; always
- `aws_acm_certificate` — for `var.domain_name` (`demo.ms3dm.tech`);
  validation method `DNS`; **must be created in `us-east-1`**
- `aws_acm_certificate_validation` — depends on the operator adding the
  DNS record at IONOS (timeout configurable; default 30 min)
- `aws_cloudfront_function` — SPA fallback: rewrite any path that
  doesn't match `/assets/*` or `/favicon.ico` etc. to `/index.html`
- `aws_cloudfront_distribution` — single origin (the S3 bucket via OAC);
  default behavior cache policy `Managed-CachingOptimized`; viewer
  protocol policy `redirect-to-https`; alternate domain name
  `demo.ms3dm.tech`; SSL certificate the ACM cert; default root object
  `index.html`; custom error responses 403 + 404 → `/index.html` 200
- `aws_s3_bucket_policy` — grants the OAC `s3:GetObject` on the bucket
- (Optional Phase 3.5) `aws_cloudfront_cache_policy` — custom

### Variables

- `env`, `tags`, `name_prefix`
- `domain_name` — `demo.ms3dm.tech`
- `bundle_path` — path to the build output (e.g.,
  `${path.module}/../../../web/dist`)

### Outputs

- `distribution_domain_name`, `distribution_id`, `distribution_arn`
- `bucket_name`, `bucket_arn`
- `acm_certificate_validation_records` — for the operator to enter at
  IONOS (one record at first apply; output is empty after validation)

### Bundle deploy

The Terraform module does NOT sync the bundle. The CI workflow
(`.github/workflows/web.yml`) does that explicitly via
`aws s3 sync web/dist/ s3://${bucket_name}/ --delete` followed by
`aws cloudfront create-invalidation --distribution-id ${distribution_id}
--paths "/*"`. Keeping deploy concerns in CI (not in Terraform) means
the bundle can be re-deployed without a `terraform apply`.

---

## 13. DNS plan (`docs/domains-and-dns.md`)

The apex `ms3dm.tech` is registered and DNS-hosted at IONOS (per the
existing site at `/Users/ryanfranklin/repos/ms3dm`). Phase 3.a adds two
records:

| Record | Type | Target | Notes |
|---|---|---|---|
| `demo.ms3dm.tech` | CNAME | `<distribution_domain_name>` (terraform output) | Permanent. Target is `dxxxxxx.cloudfront.net`. |
| `_acme-challenge.demo.ms3dm.tech` | CNAME | `<acm validation target>` (terraform output) | Temporary. Remove after ACM cert validation completes. |

DNS migration to Route53 is **not** required for Phase 3.a. Document the
records' values in `docs/domains-and-dns.md` after they're added so the
operational state is reviewable.

---

## 14. Smoke test (`scripts/smoke-web.sh`)

Tests the gateway end-to-end with all three personas. Requires a Cognito
JWT (acquired manually for the smoke test; or via Cognito's
`AdminInitiateAuth` for a service test user).

```bash
#!/usr/bin/env bash
set -euo pipefail

API_ENDPOINT=$(terraform -chdir=terraform/envs/demo output -raw api_endpoint)
JWT="${SMOKE_TEST_JWT:?Set SMOKE_TEST_JWT to a valid Cognito ID token}"

PROMPT="Show me customer 32869c51-5c92-4322-87d8-3eae02f35a14's contact info."

for PERSONA in dispatcher technician_lead owner; do
  EXTRA=""
  if [ "$PERSONA" = "technician_lead" ]; then
    EXTRA=', "service_region": "tempe-mesa"'
  fi

  echo "=== persona=$PERSONA ==="
  curl -sS -X POST "$API_ENDPOINT/ask" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -d "{\"question\": \"$PROMPT\", \"persona\": \"$PERSONA\"$EXTRA}" \
    | jq .
  echo
done
```

Pass criteria:
- Dispatcher response contains `REDACTED` markers in PII fields
- TechnicianLead response contains real-looking PII (region-scoped row
  filter is Phase 2 backlog; v1 returns full PII)
- Owner response contains real-looking PII *and* sensitivity-tagged
  columns are unmasked (revenue, costs)
- All three responses have `surface: "web"` in the corresponding
  CloudWatch invocation log entries

---

## 15. CI workflow additions

### `.github/workflows/web.yml` (NEW)

```yaml
name: web

on:
  push:
    branches: [main]
    paths:
      - "web/**"
      - "terraform/modules/web-demo/**"
      - ".github/workflows/web.yml"
  pull_request:
    paths:
      - "web/**"
      - ".github/workflows/web.yml"

permissions:
  id-token: write
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v3
        with:
          version: 9
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: "pnpm"
          cache-dependency-path: web/pnpm-lock.yaml
      - run: pnpm install --frozen-lockfile
        working-directory: web
      - run: pnpm typecheck
        working-directory: web
      - run: pnpm lint
        working-directory: web
      - run: pnpm test
        working-directory: web
      - run: pnpm build
        working-directory: web
        env:
          VITE_API_ENDPOINT: ${{ vars.VITE_API_ENDPOINT }}
          VITE_USER_POOL_ID: ${{ vars.VITE_USER_POOL_ID }}
          VITE_USER_POOL_CLIENT_ID: ${{ vars.VITE_USER_POOL_CLIENT_ID }}
          VITE_COGNITO_DOMAIN: ${{ vars.VITE_COGNITO_DOMAIN }}
          VITE_REDIRECT_SIGN_IN: ${{ vars.VITE_REDIRECT_SIGN_IN }}
          VITE_REDIRECT_SIGN_OUT: ${{ vars.VITE_REDIRECT_SIGN_OUT }}
          VITE_AWS_REGION: ${{ vars.VITE_AWS_REGION }}

  deploy:
    needs: build
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_CI_ROLE_ARN }}
          aws-region: us-east-1
      # rebuild + deploy
      - uses: pnpm/action-setup@v3
        with:
          version: 9
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: "pnpm"
          cache-dependency-path: web/pnpm-lock.yaml
      - run: pnpm install --frozen-lockfile
        working-directory: web
      - run: pnpm build
        working-directory: web
        env:
          VITE_API_ENDPOINT: ${{ vars.VITE_API_ENDPOINT }}
          # ... (same vars as build step)
      - name: Sync to S3
        run: |
          BUCKET=$(aws ssm get-parameter --name /gagent/demo/web_bucket_name --query Parameter.Value --output text)
          aws s3 sync web/dist/ s3://${BUCKET}/ --delete
          DIST=$(aws ssm get-parameter --name /gagent/demo/web_distribution_id --query Parameter.Value --output text)
          aws cloudfront create-invalidation --distribution-id ${DIST} --paths "/*"
```

> Bucket name and distribution ID land in SSM Parameter Store at
> `terraform apply` time so the workflow can pull them without a
> `terraform output` call. Add SSM parameter resources in
> `terraform/envs/demo/main.tf` for both values.

### `.github/workflows/terraform.yml` (UPDATE)

Add the three new modules to the `paths:` filter so plan runs on PRs
that touch them. No structural changes.

### `.github/workflows/eval.yml` (UNCHANGED)

The existing eval suite continues to exercise the agent-side path. The
new web smoke test (§14) is operator-run, not CI-run.

---

## 16. Open items / Phase 3.5 (deferred — do NOT pre-build)

These ship after Phase 3.a is live. Pre-building any of them violates
the acceptance gate and risks shipping nothing.

- **Streaming responses** — Lambda Function URL with response streaming,
  or API Gateway WebSocket. Reverses ADR-010 §3 deferral. Requires
  client-side refactor of `<ChatView>` to consume SSE / WS.
- **Custom API domain** — `api.demo.ms3dm.tech` via API Gateway custom
  domain mapping + ACM cert. v1 uses default `execute-api` hostname.
- **Custom Cognito Hosted UI domain** — `auth.ms3dm.tech` via Cognito
  custom domain. v1 uses Cognito-managed `*.auth.us-east-1.amazoncognito.com`.
- **Persistent chat history** — DDB table per Cognito user; `GET
  /conversations` route on the gateway; sidebar in the SPA.
- **Per-Cognito-user rate limit** — see ADR-013 (TBD). v1's IP rate
  limit is the gate.
- **CloudWatch dashboard** — pulled forward from Phase 3 backlog;
  the metrics surface is identical for Phase 3.a since invocations
  emit to the same log group with `surface="web"` tag.
- **Slack adapter** — Phase 3.b. ADR-011 (TBD) before code.
- **Mobile responsive polish** — basic responsive flex works; a
  dedicated review pass before public Substack launch.
- **OG image + share metadata** — for LinkedIn unfurls of
  `demo.ms3dm.tech`.

---

## 17. Final note for Claude Code

The acceptance gate in §6 is **the** gate. Everything else is plumbing.

If a Phase 3.a task does not advance one of the 12 numbered criteria,
push back and ask. If a criterion can't be met without a piece of work
not in this brief, raise it before adding scope.

When you finish a component (§5.1 through §5.8), commit with the
area-prefixed message convention and run the existing CI tier (per-push
unit tests + `terraform fmt` + `terraform validate`). Do not interleave
components; the dependency order is the dependency order.

The Phase 1 brief delivered 8 commits in ~3h 14m wall clock. Phase 3.a
is bigger but follows the same shape: each component is a clean unit
of work; the whole is a clean composition; the acceptance gate proves
it works end-to-end.

When §6 is green, the next post on the publication is the launch post:
*"demo.ms3dm.tech is live."* That post pays off the cliffhanger from
[the HVAC pivot post](https://ryanfranklin3.substack.com/p/why-i-changed-my-ai-agents-dataset)
and is the moment the Guardrailed Agent series goes from private
demo to public artifact.

---

## 18. References

- [[decisions/007-multi-user-identity-federation|ADR-007]] — identity layer
- [[decisions/010-gateway-architecture|ADR-010]] — API surface + WAF
- [[decisions/012-web-demo|ADR-012]] — front-end stack
- [[decisions/008-dataset-pivot-hvac-home-services|ADR-008]] — dataset
- [[decisions/009-mcp-as-reference-implementation|ADR-009]] — Shape A/B pattern
- [[repo-bootstrap-brief|repo-bootstrap-brief.md]] — Phase 1's brief; this brief mirrors its shape
- AWS API Gateway HTTP API + JWT authorizer docs
- AWS WAF v2 managed rule groups
- AWS Amplify Auth library (the JS SDK, not the framework)
- Vite + React docs
- Cognito Hosted UI docs + OIDC IdP configuration
