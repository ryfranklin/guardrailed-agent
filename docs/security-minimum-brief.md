---
title: "Security Minimum Implementation Brief — ADR-013 (WAF attach + cost alarm + JWT TTL + runbook)"
type: handoff
audience: claude-code
created: 2026-05-09
updated: 2026-05-09
tags: [handoff, claude-code, security, waf, cost-guardrails, cognito, jwt, runbook, phase-3a-gates]
---

# Security Minimum Implementation Brief — ADR-013

**Audience:** Claude Code, picking up a fresh session inside the existing
`guardrailed-agent` repo. This brief implements [[decisions/013-abuse-rate-limit-posture|ADR-013]]
— the four minimum security gates before publicizing the demo URL via
`ms3dm.tech/demo`.

**Your job, in priority order:**

1. Implement §5's four components in dependency order.
2. Use the existing repo conventions (kebab-case, no emojis, no comments
   unless *why* is non-obvious, area-prefixed commits).
3. Stop at §6's acceptance criteria. Do not extend scope.
4. Defer §8's items explicitly.

This brief is **self-contained**. It assumes Phase 3.a code is shipped
and applied (it is, per `terraform/envs/demo/terraform.tfstate`).

---

## 1. Mission

Close the four security gaps surfaced in ADR-013 so the demo URL can be
publicized via the gateway page in ADR-014 without exposing the operator
to silent cost runaway, unmitigated abuse, or panic-mode incident
response.

The four gaps:
1. WAF Web ACL not attached to the API Gateway stage
2. No CloudWatch alarm on Bedrock spend
3. JWT lifetimes are loose (1h ID/Access)
4. No incident-response runbook

This brief closes all four.

---

## 2. Decisions already made (do not redebate)

Source: [[decisions/013-abuse-rate-limit-posture|ADR-013]].

| Decision | Choice |
|---|---|
| **WAF attachment** | Add `aws_wafv2_web_acl_association` to `terraform/modules/gateway/waf.tf`; reverses the deferral in commit `f0f60cb` |
| **Cost alarm structure** | New module `terraform/modules/cost-guardrails/`; SNS topic + email subscription + two-tier `EstimatedCharges` alarms ($50/day warn, $200/day hard-stop) filtered by `ServiceName=AmazonBedrock` |
| **Cost alarm region** | `us-east-1` — required for billing alarms |
| **JWT TTL** | ID + Access tokens 60 min → 30 min; refresh stays 30 days |
| **Runbook location** | `docs/abuse-response.md` in the repo |
| **Out of scope** | Per-user rate limit, Cognito Advanced Security, CloudWatch dashboard, auto-disable-on-alarm, Bot Control |

---

## 3. Scope: in vs. out

### In scope

- One Terraform resource added to `terraform/modules/gateway/`
- One new Terraform module at `terraform/modules/cost-guardrails/`
- Two attribute changes to `terraform/modules/auth/main.tf`
- One env-layer composition update in `terraform/envs/demo/main.tf`
- One new doc at `docs/abuse-response.md`

### Out of scope (do not touch)

- Per-Cognito-user rate limiting (Lambda authorizer + DDB counter)
- Cognito Advanced Security tier configuration
- CloudWatch dashboard creation
- Any other Phase 3.5 deferred items
- The web SPA (no client-side changes needed)
- The gateway Lambda (no handler changes needed)
- ADR-011 (Slack adapter)

---

## 4. Repo additions

```
guardrailed-agent/
├── terraform/
│   ├── modules/
│   │   ├── auth/
│   │   │   └── main.tf                 UPDATED — JWT TTL changes
│   │   ├── gateway/
│   │   │   └── waf.tf                  UPDATED — WAF association added
│   │   └── cost-guardrails/            NEW
│   │       ├── main.tf
│   │       ├── variables.tf
│   │       ├── outputs.tf
│   │       └── README.md
│   └── envs/
│       └── demo/
│           └── main.tf                 UPDATED — composes cost-guardrails
└── docs/
    └── abuse-response.md               NEW
```

---

## 5. Dependency-ordered work breakdown

### 5.1 — WAF Web ACL stage association

**Input:** existing `aws_wafv2_web_acl.gateway` in
`terraform/modules/gateway/waf.tf` (or wherever the Web ACL is
declared). Existing `aws_apigatewayv2_stage` resource in the same
module.

**Output:** one new resource:

```hcl
resource "aws_wafv2_web_acl_association" "gateway_stage" {
  resource_arn = aws_apigatewayv2_stage.this.arn
  web_acl_arn  = aws_wafv2_web_acl.gateway.arn
}
```

**Note:** API Gateway HTTP API associations require the *stage* ARN,
not the API ARN. Verify the existing module exposes the stage as
`aws_apigatewayv2_stage.this` (or whatever the local name is) and
use its `.arn` attribute.

**Stop conditions:**
1. `terraform fmt -check` passes inside `terraform/modules/gateway/`
2. `terraform validate` passes
3. Module README updated to mention the WAF is now actually enforcing

**Commit:**
```
git add terraform/modules/gateway/
git commit -m "tf(gateway): attach waf web acl to api gw stage (un-defer per adr-013)"
```

### 5.2 — Cost guardrails module

**Input:** the operator's notification email (variable; passed from env layer).

**Output:** new module `terraform/modules/cost-guardrails/` with:

- `aws_sns_topic.bedrock_cost_alarms` — encrypted (SSE), tagged
- `aws_sns_topic_subscription.email` — protocol `email`, endpoint
  from variable
- `aws_cloudwatch_metric_alarm.bedrock_warn` — threshold `$50/day`
  default; metric `EstimatedCharges`; namespace `AWS/Billing`;
  dimensions `Currency=USD, ServiceName=AmazonBedrock`; period
  86400; evaluation periods 1; statistic `Maximum`; comparison
  `GreaterThanThreshold`; alarm action = SNS topic ARN
- `aws_cloudwatch_metric_alarm.bedrock_hard_stop` — threshold
  `$200/day` default; same metric/dimensions; alarm action = SNS topic
  ARN

**Variables:**
- `notification_email` (string, required, sensitive: false but
  treat-as-config) — operator's email
- `warn_threshold_usd` (number, default 50)
- `hard_stop_threshold_usd` (number, default 200)
- `tags` (map(string), default `{}`)

**Outputs:**
- `sns_topic_arn`
- `warn_alarm_arn`, `hard_stop_alarm_arn`

**Important — provider region:** AWS billing metrics live in
`us-east-1` regardless of where other resources are. The module is
already targeting `us-east-1` for the env (per ADR-002 + the existing
provider config), so no provider alias is needed. **Verify** the env
provider is `us-east-1` before instantiating.

**README** must document:
- The 6-24h lag of `EstimatedCharges` (a hard physical limit; not
  configurable)
- The two-tier alarm pattern (warn + hard-stop)
- The SNS subscription confirmation flow (operator must click the
  email confirmation link AWS sends after `terraform apply`)

**Stop conditions:**
1. `terraform fmt -check` passes
2. `terraform validate` passes (use a dummy env layer if needed; do
   not commit the dummy)
3. README documents the email-confirmation step explicitly

**Commit:**
```
git add terraform/modules/cost-guardrails/
git commit -m "tf(cost-guardrails): bedrock spend alarms with sns email"
```

### 5.3 — Lower Cognito JWT lifetimes

**Input:** existing `aws_cognito_user_pool_client` resource in
`terraform/modules/auth/main.tf`.

**Output:** the resource block updated with shorter token validity:

```hcl
resource "aws_cognito_user_pool_client" "web" {
  # ... existing config ...

  id_token_validity      = 30          # was 60
  access_token_validity  = 30          # was 60
  refresh_token_validity = 30          # unchanged (days)

  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }

  # ... rest of existing config ...
}
```

**Note:** if `token_validity_units` is not currently in the resource,
add it with the values above. If it's there, ensure the units are
correct (`minutes` for id/access, `days` for refresh).

**Stop conditions:**
1. `terraform fmt -check` passes inside `terraform/modules/auth/`
2. `terraform validate` passes
3. Module README updated to reflect the new TTLs and reference ADR-013

**Commit:**
```
git add terraform/modules/auth/
git commit -m "tf(auth): tighten cognito jwt ttls to 30 min (adr-013)"
```

### 5.4 — Compose in env layer + write the runbook + apply

**5.4a — Env composition**

Update `terraform/envs/demo/main.tf` to instantiate the
`cost-guardrails` module:

```hcl
module "cost_guardrails" {
  source = "../../modules/cost-guardrails"

  notification_email      = var.notification_email
  warn_threshold_usd      = var.cost_alarm_warn_threshold_usd
  hard_stop_threshold_usd = var.cost_alarm_hard_stop_threshold_usd
  tags                    = local.common_tags
}
```

Add the variables to `terraform/envs/demo/variables.tf` and the example
to `terraform/envs/demo/terraform.tfvars.example`.

Outputs: expose `cost_alarm_sns_topic_arn` from the env layer.

**5.4b — The runbook** at `docs/abuse-response.md`

Operator-facing playbook. Five sections:

1. **The $50/day warn alarm fired.** Steps:
   - Open CloudWatch Logs Insights against `/gagent/invocations`
   - Run a query that groups invocations by `persona`,
     `role_session_name`, and the user's Cognito `sub` (from the
     metadata field the gateway Lambda emits)
   - Identify the top-N callers of the last 24h
   - If a single user dominates: check whether their behavior is
     legitimate-but-aggressive (e.g., the operator's own testing) or
     suspicious (rapid-fire prompt injection attempts, off-topic
     queries). Decision tree included.
   - If suspicious: containment via §3 below
   - Document the trigger in `docs/abuse-incidents.md` (operator-
     created file; one incident per section)

2. **The $200/day hard-stop alarm fired.** Steps:
   - Immediate containment: disable the Cognito User Pool client
     (`aws cognito-idp update-user-pool-client --user-pool-id ... --client-id ... --no-allowed-o-auth-flows`)
     OR detach the API Gateway route (Terraform: comment out the
     `aws_apigatewayv2_route`, apply). Pick whichever you can do
     fastest.
   - This takes the demo offline. Communicate the outage on Substack +
     LinkedIn if traffic is non-trivial.
   - Investigate per §1.
   - Recovery: re-enable Cognito client / re-add the route after
     the abuse source is contained.

3. **Containment options (graduated).** Listed from least invasive
   to most:
   - Disable a single Cognito user: `aws cognito-idp admin-disable-user`
   - Lower the WAF IP rate-rule from 100 → 20 req/5min (terraform
     edit + apply; ~5 min)
   - Add a temporary WAF IPSet rule blocking the offending IP
     (terraform edit + apply; ~5 min)
   - Disable the Cognito User Pool client (kicks all users out)
   - Detach the API Gateway route (kicks the entire demo down)

4. **A spike in CloudWatch invocations** (operator notices via
   periodic check, not alarm-driven). Same diagnostic queries as §1.
   Lower-urgency containment.

5. **A user reports the agent saying something problematic.**
   Decision tree:
   - Is it a Bedrock Guardrails miss? Check the trace in
     `/gagent/invocations`. If yes: amend the Guardrails policy
     (`terraform/modules/guardrails/main.tf`); add a red-team case to
     `eval/prompts/red_team.yaml` reproducing the issue.
   - Is it a Lake Formation visibility leak? **Should be impossible**
     given the synthetic-data property — but if it ever happens, treat
     as a Sev-1: take the demo offline immediately, audit the LF tag
     attachments, file a post-mortem.
   - Is it just an awkward-but-non-harmful generation? File a note;
     potentially refine the system prompt; not an incident.

   Each section includes:
   - Diagnostic queries (real CloudWatch Logs Insights queries
     copy-pasteable)
   - Containment commands (real `aws` CLI commands)
   - Recovery steps
   - Post-mortem template (5-line structure)

**5.4c — Apply + verification**

Run from `terraform/envs/demo/`:
```bash
terraform plan
# review the plan; expect ~5-7 resource changes:
#   + aws_wafv2_web_acl_association.gateway_stage  (in module.gateway)
#   + module.cost_guardrails.aws_sns_topic.bedrock_cost_alarms
#   + module.cost_guardrails.aws_sns_topic_subscription.email
#   + module.cost_guardrails.aws_cloudwatch_metric_alarm.bedrock_warn
#   + module.cost_guardrails.aws_cloudwatch_metric_alarm.bedrock_hard_stop
#   ~ aws_cognito_user_pool_client.web  (in module.auth — TTL changes)
#   ~ aws_apigatewayv2_stage  (the WAF association may show as a
#     dependency-driven update; that's fine)

terraform apply
# operator confirms with "yes" after reviewing
```

**Post-apply manual steps (operator):**
1. Open the email AWS sent to confirm the SNS subscription. Click the
   confirmation link. (Subscription stays in `PendingConfirmation`
   state until clicked; alarms can't notify until confirmed.)
2. Verify the alarm transitions to `OK` state in the CloudWatch
   console (it should — there's no Bedrock spend triggering it).
3. Verify WAF is enforcing: hit the API endpoint > 100 times in 5
   minutes from one IP using `curl`; the next request returns HTTP 429.

**Stop conditions for 5.4:**
1. `terraform plan` shows exactly the expected changes (no
   surprises in the diff)
2. `terraform apply` succeeds
3. Operator confirms SNS subscription email was clicked
4. WAF rate-limit verified by curl loop
5. `docs/abuse-response.md` is committed

**Commits:**
```
# After the docs:
git add docs/abuse-response.md
git commit -m "docs: abuse-response runbook (adr-013)"

# After the env composition:
git add terraform/envs/demo/
git commit -m "tf(demo): compose cost-guardrails module + jwt ttl propagation"
```

---

## 6. Acceptance criteria

ADR-013 ships when **all** of the following are true.

1. `terraform/modules/gateway/` includes the
   `aws_wafv2_web_acl_association` resource attaching the Web ACL to
   the API Gateway stage.
2. `terraform/modules/cost-guardrails/` exists with the two alarms +
   SNS topic + email subscription resources.
3. `terraform/modules/auth/` has the JWT TTLs lowered to 30 minutes
   for ID and access tokens.
4. `terraform/envs/demo/main.tf` instantiates the `cost-guardrails`
   module.
5. `docs/abuse-response.md` is committed with all five sections.
6. `terraform plan` shows exactly the expected change set (no
   unexpected resource changes).
7. `terraform apply` succeeds end-to-end.
8. The SNS email subscription is confirmed (operator-side step).
9. The CloudWatch alarms transition to `OK` state.
10. WAF rate-limit enforcement verified: a curl loop > 100 req / 5
    min from one IP yields HTTP 429 on subsequent requests.
11. JWT TTL change verified: signing in, decoding the JWT (jwt.io or
    `python -c "import jwt; ..."`), confirming `exp - iat == 1800`
    (30 minutes).
12. The runbook reads cleanly — operator can follow each section's
    steps without external context.

When 1-12 are green, the gateway page (ADR-014) is unblocked.

---

## 7. Sequencing

This is a 1-2 hour body of work, not a multi-weekend chunk. Sequence
the four prompts as a single sitting:

| Order | Prompt | What ships |
|---|---|---|
| 1 | Kickoff + summary | Read brief; summarize all four components; await go |
| 2 | §5.1 + §5.3 | WAF attachment + JWT TTL (both small TF changes; combined for momentum) |
| 3 | §5.2 + §5.4a | Cost-guardrails module + env composition |
| 4 | §5.4b + §5.4c | Runbook docs + apply + verify |

Four prompts. Stop-and-confirm gate between each. Total: 1-2 hours
including the operator's apply and verification steps.

---

## 8. Open items / Phase 3.5+

These are explicitly NOT in this brief. Do not pre-build them.

- **Per-Cognito-user rate limiting** — Lambda authorizer with DDB
  counter. ~half a day. Trigger: first abuse incident traceable to
  one user, or daily active users > 25.
- **Cognito Advanced Security Features** — paid tier. Trigger: >100
  monthly active users.
- **CloudWatch dashboard** — pulled forward from Phase 3.5 deferrals.
  Trigger: any time after this brief ships.
- **Auto-disable-on-alarm Lambda** — risky (false positives); defer
  until manual containment is genuinely a bottleneck.
- **WAF Bot Control** — paid; trigger when managed rules show
  observable miss rate.

---

## 9. Handoff prompts (sequential)

The four prompts to paste into the project's Claude Code session,
one at a time, with stop-and-confirm gates between each.

### Prompt 1 — Kickoff + summarize all four components

````
ADR-013 — close the four security gaps before publicizing the demo.

The implementation brief is the source of truth. Read it first:
/Users/ryanfranklin/ms3dm.tech/consulting/guardrailed-agent/security-minimum-implementation-brief.md

Then read ADR-013:
/Users/ryanfranklin/ms3dm.tech/consulting/guardrailed-agent/decisions/013-abuse-rate-limit-posture.md

Both files are vault-side; copy the brief into the repo at
docs/security-minimum-brief.md and commit:
  git add docs/security-minimum-brief.md
  git commit -m "docs: adr-013 security minimum brief"

Then summarize the four components back to me in 8-12 bullets:
  - §5.1 WAF stage association — what one resource you'll add and where
  - §5.2 Cost guardrails — the module shape and the two alarms
  - §5.3 JWT TTL — the exact attribute changes
  - §5.4 Env composition + runbook + apply
  - Any deviations from the brief's spec you propose
  - Any open questions for me

Do NOT write any code yet. Do NOT modify any files except for the brief
copy + commit above. Wait for my go.
````

### Prompt 2 — §5.1 + §5.3 (WAF attach + JWT TTL)

````
Go. Implement §5.1 (WAF stage association) AND §5.3 (JWT TTL change)
together — both are small, both are TF-only, both are independent of
the other prompts.

§5.1: add aws_wafv2_web_acl_association to terraform/modules/gateway/
attaching the existing Web ACL to the existing API Gateway HTTP API
stage. Verify the existing local resource names by reading the
module first.

§5.3: lower id_token_validity and access_token_validity in the
aws_cognito_user_pool_client resource at terraform/modules/auth/
from 60 to 30 minutes. Add or update the token_validity_units block.
Refresh token stays at 30 days.

Update both module READMEs to reflect the changes (small additions,
not rewrites).

Stop conditions:
  - terraform fmt -check passes in BOTH modules
  - terraform validate passes (use a temporary dummy env to validate
    if needed; do NOT commit the dummy)

Two commits:
  git add terraform/modules/gateway/
  git commit -m "tf(gateway): attach waf web acl to api gw stage (un-defer per adr-013)"

  git add terraform/modules/auth/
  git commit -m "tf(auth): tighten cognito jwt ttls to 30 min (adr-013)"

Then post a summary: what shipped in each module, any deviations, what
§5.2 (cost-guardrails) will need from these. Do NOT start §5.2.
````

### Prompt 3 — §5.2 + §5.4a (cost-guardrails module + env composition)

````
Go. Implement §5.2 (cost-guardrails module) and §5.4a (env composition).

§5.2: new module at terraform/modules/cost-guardrails/. Per §5.2 of
the brief: SNS topic (encrypted), email subscription, two
EstimatedCharges alarms (warn $50/day default, hard-stop $200/day
default). Variables, outputs, README.

The README MUST document the SNS email-confirmation flow (operator
must click the AWS confirmation email after terraform apply, or
alarms can't notify).

§5.4a: instantiate the module in terraform/envs/demo/main.tf.
Add notification_email + cost_alarm_warn_threshold_usd +
cost_alarm_hard_stop_threshold_usd to terraform/envs/demo/variables.tf
and terraform/envs/demo/terraform.tfvars.example. Expose
cost_alarm_sns_topic_arn from the env layer outputs.

Stop conditions:
  - terraform fmt -check passes in module + env
  - terraform validate passes in env (after init — env can run
    init since it has the providers)
  - DO NOT terraform apply yet — that happens in prompt 4

Two commits:
  git add terraform/modules/cost-guardrails/
  git commit -m "tf(cost-guardrails): bedrock spend alarms with sns email"

  git add terraform/envs/demo/
  git commit -m "tf(demo): compose cost-guardrails module + jwt ttl propagation"

Then post a summary: the alarm thresholds, the SNS confirmation flow,
the variables added to the env, and what §5.4b/c will need. Do NOT
write the runbook or apply yet.
````

### Prompt 4 — §5.4b + §5.4c (runbook + apply + verify)

````
Go. Final prompt. Implement §5.4b (runbook) and §5.4c (apply + verify).

§5.4b: write docs/abuse-response.md with all five sections from the
brief. Make it operator-readable — no jargon-without-context, real
copy-pasteable CloudWatch Logs Insights queries, real aws CLI commands
for containment. Each section needs: trigger → diagnostics →
containment → recovery → post-mortem template (5-line structure).

Commit:
  git add docs/abuse-response.md
  git commit -m "docs: abuse-response runbook (adr-013)"

§5.4c: run terraform plan in terraform/envs/demo. Show me the plan
output. Wait for my "go" before applying.

After my go: terraform apply. Confirm only after I say "yes."

Once apply succeeds:
  1. Show me the new outputs (cost_alarm_sns_topic_arn).
  2. Tell me to check my email and click the SNS subscription
     confirmation link.
  3. Wait for me to confirm the email is clicked.

After my "email confirmed":
  1. Run a verification curl loop against the API endpoint to confirm
     the WAF rate-limit kicks in (curl -X POST ... 110 times in a
     loop; expect 429s on the last few). Show me the output.
  2. Decode a fresh JWT (after sign-in via Cognito Hosted UI) and
     confirm the exp - iat delta is 1800 seconds (30 min).
  3. Walk through §6 of the brief — the 12 acceptance criteria — and
     report each as green/red.

Do NOT:
  - Apply without showing me the plan first
  - Skip the SNS email-confirmation wait step
  - Declare ADR-013 done if any acceptance criterion is red
  - Update the project README or task list — that's my side
````

### After Prompt 4

When all 12 acceptance criteria are green:
- ADR-013 is shipped
- The gateway page (ADR-014) is unblocked — operator's call when to start
- The Substack #11 launch post is unblocked once the gateway page (or
  direct-to-demo posture) is decided

---

## 10. References

- [[decisions/013-abuse-rate-limit-posture|ADR-013]] — the
  architectural decision this brief executes
- [[decisions/007-multi-user-identity-federation|ADR-007]] — the JWT
  TTL spec being amended
- [[decisions/010-gateway-architecture|ADR-010]] — the gateway whose
  WAF is being attached
- [[decisions/014-demo-gateway-page|ADR-014]] — the gateway page this
  ADR unblocks
- [[phase-3a-implementation-brief|Phase 3.a brief]] — the source of
  the resources being modified
- Commit `f0f60cb` — the deferral being undone
- AWS WAF v2 web_acl_association docs
- AWS CloudWatch billing alarm docs (`EstimatedCharges` metric)
- AWS Cognito User Pool client token validity docs
