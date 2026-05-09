---
title: "ADR-013 §5.1 addendum — WAF stage association remains deferred"
type: addendum
created: 2026-05-09
updated: 2026-05-09
tags: [adr-013, waf, gateway, http-api, deferral]
---

# ADR-013 §5.1 addendum — WAF stage association remains deferred

## What changed

ADR-013 §5.1 (and the corresponding section of `docs/security-minimum-brief.md`)
proposed un-deferring the WAF Web ACL → API Gateway stage association first
deferred in commit `f0f60cb`. On re-verification in May 2026 against the
current AWS documentation, the underlying constraint still holds: AWS WAFv2
**does not support API Gateway HTTP API (v2) stages**.

The Web ACL stays provisioned with its three rules (AWS managed
`CommonRuleSet`, `KnownBadInputsRuleSet`, and a 100 req / 5 min per-IP
rate rule). It emits CloudWatch metrics. **It is not evaluating any
request.** ADR-013 §5.1 cannot ship as written.

## What was verified

Source:
[docs.aws.amazon.com/waf/latest/developerguide/how-aws-waf-works-resources.html](https://docs.aws.amazon.com/waf/latest/developerguide/how-aws-waf-works-resources.html)

The supported regional resource types for an AWS WAFv2 Web ACL are:

- Amazon API Gateway **REST API** (v1)
- Application Load Balancer
- AWS AppSync GraphQL API
- Amazon Cognito user pool
- AWS App Runner service
- AWS Verified Access instance
- AWS Amplify

API Gateway **HTTP API (v2) is not in the list.** `AssociateWebACL` returns
`WAFInvalidParameterException` for an HTTP API stage ARN. The
[API Gateway-side guide for WAF](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-control-access-aws-waf.html)
likewise covers REST APIs only.

## Three options to unblock

Tracked here so the next session can pick one up without rediscovering the
constraint:

1. **Wait for AWS.** Cheapest, least predictable. AWS has been adding HTTP
   API support to other regional services over time; a re-check every few
   quarters costs nothing.
2. **Migrate gateway HTTP API → REST API.** Multi-day. Touches
   `terraform/modules/gateway/` extensively (REST API uses
   `aws_api_gateway_rest_api` + `aws_api_gateway_stage` + a different
   authorizer model than HTTP API's native JWT authorizer), the smoke
   test, and possibly the gateway Lambda integration shape. Loses HTTP
   API's lower latency / lower cost.
3. **Front the HTTP API with CloudFront, attach WAF to CloudFront.** New
   TF module. Reworks DNS at IONOS so `demo.ms3dm.tech` points at the
   CloudFront distribution; the API Gateway endpoint becomes the
   distribution's origin. Couples to ADR-014 (gateway page), since the
   marketing page and the API would now both sit behind CloudFront.

Option 3 is the most architecturally clean answer if AWS support doesn't
arrive. Option 1 is the right default until the demo's traffic profile
makes the absence of an enforcing WAF an actual problem, not a theoretical
one.

## What ships under ADR-013 instead

- §5.2 cost-guardrails module (Bedrock spend alarms — replaces the
  cost-runaway gap that §5.1 was also implicitly mitigating)
- §5.3 JWT TTL tightening (60 → 30 min on ID + access tokens)
- §5.4 env composition + `docs/abuse-response.md` runbook + apply

The combination — alarms on cost, half-life on tokens, runbook for
response — closes the `realistic` portion of ADR-013's risk envelope
even with §5.1 deferred. The synthetic-data property of the demo
continues to bound the *information* risk to ~zero. The remaining gap
is per-IP burst abuse with no rate-limit fallback; the runbook in §5.4
addresses that with manual containment via Cognito client disable
or API Gateway route detach.

## When to revisit

- Any AWS announcement adding HTTP API v2 to the WAFv2 supported-resource
  list. Re-test by uncommenting the resource block in
  `terraform/modules/gateway/waf.tf` and running `terraform plan`.
- A first abuse incident traceable to per-IP burst that the cost alarm
  catches too slowly. That makes Option 3 (CloudFront fronting) worth
  the scope.
- Ahead of any decision to expand the demo's exposure beyond
  `ms3dm.tech/demo` (e.g., embedding the demo in a partner site, opening
  it to anonymous-no-Cognito traffic). Both pull WAF enforcement from
  "nice to have" to "required."

## References

- [docs/security-minimum-brief.md](./security-minimum-brief.md) §5.1 — the proposal this addendum modifies
- `terraform/modules/gateway/waf.tf` — the deferred resource block + comment
- ADR-013 (vault-side, `consulting/guardrailed-agent/decisions/013-abuse-rate-limit-posture.md`)
- ADR-010 §5 — the gateway / WAF rule set spec
- Commit `f0f60cb` — the original deferral
