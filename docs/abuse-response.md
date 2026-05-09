---
title: "Abuse-response runbook (ADR-013 §5.4)"
type: runbook
created: 2026-05-09
updated: 2026-05-09
audience: operator
tags: [runbook, abuse, cost, waf, cognito, adr-013]
---

# Abuse-response runbook

Operator-facing playbook for the five abuse / incident scenarios ADR-013
anticipates. Each section follows the same structure:

- **Trigger** — what fires this section.
- **Diagnostics** — copy-pasteable CloudWatch Logs Insights queries and
  `aws` CLI commands.
- **Containment** — graduated, from least invasive (single user) to most
  (demo offline).
- **Recovery** — how to come back up.
- **Post-mortem template** — 5 lines, append to `docs/abuse-incidents.md`
  (operator-created file; one incident per section).

Target response time: **5–15 minutes** from page to first containment
action. Skim the relevant section in full before acting.

## Reference: log groups and resource names

The demo uses the `gagent-` prefix in `us-east-1`. Substitute for client
deployments.

| Resource | Name | Where it shows up |
|---|---|---|
| API Gateway access log group | `/aws/apigateway/gagent-gateway-demo` | One log entry per request. Has `jwtSub`, `jwtEmail`, `ip`, `requestId`, `status`, `routeKey`. |
| gagent invocation log group | `/gagent/invocations` | One JSON line per Bedrock-Agent call. Has `persona`, `session_id`, `role_session_name`, `surface`, `tools_called`, `guardrail_blocks`, `duration_seconds`, truncated `input` and `output`. |
| Cognito User Pool | `gagent-demo` | User listings, admin-disable. |
| Cognito SPA client | `gagent-demo-web` | Disable to revoke all sessions. |
| API Gateway HTTP API | name `gagent-gateway-demo`; ID via `terraform output api_id` | Detach a route to take the demo offline. |
| WAF Web ACL | `gagent-gateway-demo` | Provisioned but **not enforcing** — see ADR-013 §5.1 deferral. |

Set these once per shell:

```
export AWS_PROFILE=gagent-demo
export AWS_REGION=us-east-1
export USER_POOL_ID=$(cd terraform/envs/demo && terraform output -raw cognito_user_pool_id)
export CLIENT_ID=$(cd terraform/envs/demo && terraform output -raw cognito_user_pool_client_id)
export API_ID=$(cd terraform/envs/demo && terraform output -raw api_endpoint | sed 's|https://||;s|\..*||')
```

---

## §1. The $50/day warn alarm fired

### Trigger

CloudWatch alarm `gagent-bedrock-cost-warn-demo` transitions to
`ALARM`. Operator gets an email from
`AWS Notifications <no-reply@sns.amazonaws.com>`. Bedrock estimated
charges crossed $50 in a 24h window — about 10× the demo's normal
weekend steady-state.

The `EstimatedCharges` metric lags 6–24h. By the time you're reading
this, the spike already happened — the question is whether it's still
happening and who caused it.

### Diagnostics

**Step 1 — Is it still happening?** Look at the last hour of API
calls in the API Gateway access log:

```
aws logs start-query \
  --log-group-name /aws/apigateway/gagent-gateway-demo \
  --start-time $(($(date +%s) - 3600)) \
  --end-time $(date +%s) \
  --query-string 'fields @timestamp, jwtSub, jwtEmail, ip, routeKey, status
                  | filter routeKey = "POST /ask"
                  | stats count() as n by jwtSub, jwtEmail
                  | sort n desc
                  | limit 20' \
  --region us-east-1
```

(Then `aws logs get-query-results --query-id <id from above>`.)

If one user dominates (>50% of calls), they're the cause. Note their
`jwtSub` and `jwtEmail`.

**Step 2 — What are they running?** Pull the recent invocations from
the gagent log group, joined by session_id (lossy — see note):

```
aws logs start-query \
  --log-group-name /gagent/invocations \
  --start-time $(($(date +%s) - 3600)) \
  --end-time $(date +%s) \
  --query-string 'fields @timestamp, persona, session_id, surface, duration_seconds, guardrail_blocks
                  | filter surface = "web"
                  | stats count() as n, sum(duration_seconds) as total_seconds, sum(guardrail_blocks) as blocks by persona
                  | sort n desc' \
  --region us-east-1
```

Useful tells:

- **`guardrail_blocks` > 0 across many calls** — likely prompt-injection
  attempts. Suspicious.
- **`tools_called` field empty across many calls** — the agent isn't
  doing data work; could be off-topic chatter or red-teaming.
- **Many short calls in rapid succession** — automation, not a human.
- **One steady, slow user** — possibly the operator's own load testing
  or an enthusiastic legitimate user.

> **Note on correlation.** The `/gagent/invocations` log doesn't carry
> the JWT `sub`. Correlation between API Gateway access (which has
> `jwtSub`) and invocations (which has `session_id`/`persona`) is
> currently lossy — you can identify the noisy user by `jwtSub` and the
> noisy persona by `persona`, but not always tie them together. If
> persona attribution matters for an incident, the next iteration of
> ADR-013 should add `jwtSub` to the invocation metadata; in the
> meantime, the ip + jwtSub pair from access log is enough to act.

### Containment

Pick the least-invasive option that fits.

**A. Single suspicious user — admin-disable in Cognito.** Kicks them
out, denies new sessions, doesn't touch anyone else.

```
aws cognito-idp admin-disable-user \
  --user-pool-id $USER_POOL_ID \
  --username '<jwtSub from diagnostics>' \
  --region us-east-1
```

(For email-based pools, `--username` accepts either the email or
the `sub` UUID.)

**B. Suspicious-but-not-confirmed pattern — keep an eye on it.**
Don't touch anything; re-run §1 diagnostics every hour. The hard-stop
alarm at $200/day is your safety net.

**C. Traffic from a single IP — note the IP and skip to §3** for the
graduated containment list.

### Recovery

- After admin-disable: the user can no longer sign in. To re-enable
  later (e.g., it turned out to be legitimate): `admin-enable-user` with
  the same args.
- Re-test that the demo still works for other users by signing in
  yourself and running one query.
- Wait 24h for the next billing-metric tick; confirm spend has fallen
  below the warn threshold and the alarm transitions to `OK` (you'll
  get an OK email).

### Post-mortem template

Append to `docs/abuse-incidents.md`:

```
## <YYYY-MM-DD HH:MM UTC> — warn-alarm fire
- Trigger: $50/day warn alarm; <peak USD/day from billing console>
- Cause: <one-sentence root cause; e.g. "single user X running automation against /ask">
- Containment: <action taken>; <minutes from page to action>
- Damage: <approx total $ over the spike>
- Followup: <change to make, if any>
```

---

## §2. The $200/day hard-stop alarm fired

### Trigger

CloudWatch alarm `gagent-bedrock-cost-hard-stop-demo` transitions to
`ALARM`. **This is a Sev-2.** $200/day is ~40× steady-state.
Containment first, investigation second.

### Diagnostics

Skip until containment is in. The same queries as §1 will work
afterwards.

### Containment (immediate — pick the fastest)

**Option A — disable the SPA client.** Takes the demo offline for
everyone in <30 seconds.

```
aws cognito-idp update-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $CLIENT_ID \
  --no-allowed-o-auth-flows-user-pool-client \
  --region us-east-1
```

This revokes the SPA's ability to use OAuth flows. New logins fail
immediately; existing JWTs continue to work until they expire (≤30
min after ADR-013 §5.3). The Bedrock spend tap closes within minutes
as in-flight users' tokens expire.

**Option B — detach the API Gateway routes.** Slower (Terraform
roundtrip), more disruptive, but ironclad.

```
# In terraform/envs/demo, comment out:
#   resource "aws_apigatewayv2_route" "post_ask"
#   resource "aws_apigatewayv2_route" "post_preview"
# Then:
cd terraform/envs/demo
terraform apply -auto-approve
```

This makes `POST /ask` and `POST /preview` 404 — no Bedrock call can
happen at all. Use when option A doesn't work fast enough or you
suspect the JWT layer itself is bypassed.

**Option C — communicate the outage.** If traffic is non-trivial and
the demo has been publicized:

- LinkedIn / Substack short post: "Demo temporarily offline,
  investigating an unusual usage pattern. Back shortly." No technical
  detail.
- Don't post until containment is confirmed; otherwise you're
  advertising an active incident.

### Recovery

1. **Investigate before re-enabling.** Run §1 diagnostics. Identify the
   abuser by `jwtSub`/`jwtEmail`/`ip`.
2. **Disable the abusing user** (§1 containment A).
3. **Re-enable the SPA client** (or uncomment the routes):

   ```
   aws cognito-idp update-user-pool-client \
     --user-pool-id $USER_POOL_ID \
     --client-id $CLIENT_ID \
     --allowed-o-auth-flows-user-pool-client \
     --allowed-o-auth-flows code \
     --allowed-o-auth-scopes email openid profile \
     --region us-east-1
   ```

4. **Smoke test** by signing in with a different account and running
   one query.
5. **Wait 24h** for the metric to refresh. Confirm both alarms back to
   `OK`.

### Post-mortem template

```
## <YYYY-MM-DD HH:MM UTC> — hard-stop alarm fire (Sev-2)
- Trigger: $200/day hard-stop; <peak USD/day>
- Cause: <root cause>
- Containment: <option A/B>; <minutes from alarm to demo offline>
- Damage: <approx total $>; <was the demo publicly down? for how long?>
- Followup: <change to ADR-013 thresholds, runbook, or pull-forward of per-user rate limit?>
```

---

## §3. Containment options (graduated)

Reference list from least invasive to most. Use when you're picking
between options in §1 or §2, or when you notice trouble out of band.

| # | Option | Blast radius | Apply time |
|---|---|---|---|
| 1 | Admin-disable a single Cognito user | one user | <30s, CLI |
| 2 | Lower the WAF IP rate rule (100 → 20 req/5min) | per-IP | ~5 min, terraform apply (note: WAF not enforcing today; see ADR-013 §5.1 addendum) |
| 3 | Add a temporary WAF IPSet rule blocking the offending IP | one IP | ~5 min, terraform apply (same caveat) |
| 4 | Disable the SPA Cognito client | every signed-in user | <30s, CLI |
| 5 | Detach the `POST /ask` + `POST /preview` routes | every caller — demo offline | ~3 min, terraform apply |

### Commands

**1 — disable user:**

```
aws cognito-idp admin-disable-user \
  --user-pool-id $USER_POOL_ID \
  --username '<sub or email>' \
  --region us-east-1
```

**2 — lower rate limit:** edit `gateway_rate_limit_per_5min` in
`terraform/envs/demo/terraform.tfvars` (or just override at apply
time with `-var=gateway_rate_limit_per_5min=20`), then
`terraform apply`. **Caveat:** the WAF Web ACL is provisioned but
not enforcing on the HTTP API stage — see
[docs/adr-013-waf-association-deferral.md](./adr-013-waf-association-deferral.md).
Lowering the limit is harmless but won't change runtime behavior.

**3 — IPSet block:** add to `terraform/modules/gateway/waf.tf`:

```hcl
resource "aws_wafv2_ip_set" "block" {
  name               = "${local.waf_name}-blocked-ips"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = ["198.51.100.42/32"]  # the offending IP
}
# add a rule under aws_wafv2_web_acl.this referencing this set
```

Same caveat re: WAF deferral.

**4 — disable SPA client:** see §2 option A.

**5 — detach routes:** see §2 option B.

---

## §4. A spike in CloudWatch invocations (no alarm)

### Trigger

You notice — out of band, not from an alarm — that
`/gagent/invocations` is busier than usual. Maybe you're watching the
console for an unrelated reason. The cost alarm hasn't fired (yet).

This is the lower-urgency cousin of §1. Investigate before it does
fire.

### Diagnostics

Same queries as §1 — substitute the time window for whatever you're
seeing:

```
aws logs start-query \
  --log-group-name /aws/apigateway/gagent-gateway-demo \
  --start-time $(($(date +%s) - 21600)) \
  --end-time $(date +%s) \
  --query-string 'fields @timestamp, jwtSub, jwtEmail, routeKey
                  | filter routeKey = "POST /ask"
                  | stats count() as n by jwtSub
                  | sort n desc' \
  --region us-east-1
```

Decision tree:

- One user > 100 calls / 6h → likely automation. Containment §1A.
- Steady distribution across many users, all within ~5 calls each →
  legitimate engagement spike (e.g. a Substack post landed). No
  action.
- One user with `guardrail_blocks > 0` repeatedly → red-teaming.
  Containment §1A; consider adding a regression case to
  `eval/prompts/red_team.yaml`.

### Containment

Identical to §1; pick the least invasive option that fits.

### Recovery

If you containment-acted: re-test the demo. If you didn't: nothing.

### Post-mortem template

```
## <YYYY-MM-DD HH:MM UTC> — invocation spike (no alarm)
- Trigger: noticed N calls in <window>, normal is M
- Cause: <root cause or "legitimate spike — Substack post">
- Containment: <action or "none, monitored">
- Followup: <change to alarm thresholds? add to publicized-events log?>
```

---

## §5. A user reports the agent saying something problematic

### Trigger

Direct user feedback (Substack DM, LinkedIn comment, email): "the
agent told me X and that's wrong / offensive / leaked something."

Treat as the highest-priority kind of report — these are the ones
that produce screenshots that get posted publicly.

### Diagnostics

**Step 1 — find the trace.** Get the user's email or `sub`, the
approximate time, and ideally the question they asked:

```
# Lookup the user's sub by email if needed:
aws cognito-idp list-users \
  --user-pool-id $USER_POOL_ID \
  --filter "email = \"<user_email>\"" \
  --region us-east-1 \
  --query 'Users[0].Username'
```

**Step 2 — pull the actual interaction.** The full input/output is in
`/gagent/invocations`:

```
aws logs start-query \
  --log-group-name /gagent/invocations \
  --start-time <reported_time - 600> \
  --end-time <reported_time + 600> \
  --query-string 'fields @timestamp, persona, session_id, input, output, guardrail_blocks, tools_called
                  | filter input like /<question fragment user remembers>/
                  | sort @timestamp desc
                  | limit 20' \
  --region us-east-1
```

(`input` is truncated at 4000 chars, `output` at 16000. Sufficient for
diagnosis.)

**Step 3 — classify.** Decision tree:

- **Bedrock Guardrails miss.** `guardrail_blocks` was 0 but should
  have been ≥ 1. Treat as a guardrail policy gap.
- **Lake Formation visibility leak.** The output references data the
  caller's persona shouldn't see (e.g. dispatcher seeing
  `service_region`-restricted rows). **This should be impossible**
  given the LF + ABAC setup; if it's real, it's Sev-1.
- **Awkward but non-harmful.** The agent said something unhelpful or
  weird but not actually wrong/offensive/leaky. File a note,
  potentially refine the system prompt later, not an incident.

### Containment

Per classification:

**Guardrails miss.**

1. Reproduce locally:
   ```
   cd eval && python runner.py --prompt "<the user's question>"
   ```
2. Add a red-team case to `eval/prompts/red_team.yaml` capturing the
   pattern.
3. Amend `terraform/modules/guardrails/main.tf` to extend the policy.
   Apply.
4. Re-run the eval; confirm the case is now blocked.

**Lake Formation leak — Sev-1.**

1. Take the demo offline immediately (§3 option 4 or 5).
2. Audit the LF tag attachments:
   ```
   aws lakeformation list-lf-tags --region us-east-1
   aws lakeformation get-resource-lf-tags \
     --resource '{"Database":{"Name":"guardrailed_agent_demo"}}' \
     --region us-east-1
   ```
3. Audit the persona role session-tag plumbing in
   `terraform/modules/identity/`.
4. File a post-mortem before bringing the demo back up.

**Awkward but non-harmful.**

No containment. Take notes; reply to the user thanking them; don't
let it block other work.

### Recovery

- Guardrails: confirm the eval suite still passes after the policy
  change.
- LF leak: only re-publicize the demo after the root cause is fixed
  AND a regression test exists.
- Awkward: nothing to recover.

### Post-mortem template

```
## <YYYY-MM-DD HH:MM UTC> — user report
- Trigger: <user> reported <one-line summary>
- Classification: guardrails miss / LF leak / awkward
- Cause: <root cause>
- Containment: <change made; PR or commit ref>
- Followup: <regression test added? ADR amended?>
```

---

## Templates and references

- `docs/abuse-incidents.md` — operator-created file. One incident per
  section. Use the templates above verbatim.
- ADR-013 (vault) — the architectural decision driving this runbook.
- `docs/security-minimum-brief.md` — the implementation brief.
- `docs/adr-013-waf-association-deferral.md` — why the WAF rate-rule
  options in §3 are inert today.
- `terraform/modules/cost-guardrails/README.md` — alarm thresholds and
  the SNS confirmation flow.
- `terraform/modules/auth/README.md` — Cognito user-pool config; JWT
  TTLs (30 min ID/access).
- AWS Cognito admin actions —
  [docs.aws.amazon.com/cli/latest/reference/cognito-idp/](https://docs.aws.amazon.com/cli/latest/reference/cognito-idp/).
- CloudWatch Logs Insights query syntax —
  [docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html).
