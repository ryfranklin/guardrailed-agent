# cost-guardrails

CloudWatch billing alarms on Bedrock spend, plus the SNS topic +
email subscription that fan out alarm transitions to the operator.
Implements [ADR-013](../../../../consulting/guardrailed-agent/decisions/013-abuse-rate-limit-posture.md)
§5.2 — closes the silent-cost-runaway gap surfaced when ADR-013 was
written.

Source-of-truth specs:
[docs/security-minimum-brief.md §5.2](../../../docs/security-minimum-brief.md).

## Resources

| Resource | Purpose |
|---|---|
| `aws_sns_topic.bedrock_cost_alarms` | Fan-out for alarm state transitions. SSE on with the AWS-managed `alias/aws/sns` key. |
| `aws_sns_topic_subscription.email` | Operator email subscription. Confirmation flow described below. |
| `aws_cloudwatch_metric_alarm.bedrock_warn` | Warn-tier alarm at `warn_threshold_usd` USD/day on `AWS/Billing.EstimatedCharges` filtered to `ServiceName=AmazonBedrock`. |
| `aws_cloudwatch_metric_alarm.bedrock_hard_stop` | Hard-stop-tier alarm at `hard_stop_threshold_usd` USD/day, same metric/dimensions. |

## Inputs

| Variable | Required | Default | Notes |
|---|---|---|---|
| `env` | yes | — | Environment suffix on resource names (e.g. `demo`). |
| `name_prefix` | no | `gagent-` | Resource-name prefix. |
| `notification_email` | yes | — | Operator email. Validated to a basic email shape. |
| `warn_threshold_usd` | no | 50 | USD/day. Calibrated to ~10x normal demo steady-state spend. |
| `hard_stop_threshold_usd` | no | 200 | USD/day. ALARM transition triggers the runbook's immediate-containment path. |
| `tags` | no | `{}` | Common resource tags. |

## Outputs

| Output | Notes |
|---|---|
| `sns_topic_arn` | Subscribe additional endpoints (Slack webhook, PagerDuty) here in client deployments. |
| `warn_alarm_arn`, `hard_stop_alarm_arn` | Cross-stack references; the alarms are already wired to `sns_topic_arn`. |

## Required region: `us-east-1`

The `EstimatedCharges` metric in the `AWS/Billing` namespace is **only**
emitted in `us-east-1`. Instantiate this module under a provider in
`us-east-1` (which the demo env already does — see
`terraform/envs/demo/main.tf`'s `provider "aws"` block). If composed
under another region the alarms will silently never breach because
the metric will not exist there.

For multi-region client deployments, run this module from a
`us-east-1`-aliased provider and wire its outputs into the rest of the
stack. No provider alias is needed for the demo env because the env's
default provider is already `us-east-1`.

## Two-tier alarm pattern

Two alarms watch the same metric/dimensions at different thresholds:

- **Warn tier** ($50/day default) — investigate (per
  `docs/abuse-response.md` §1). Probably one heavy user; check whether
  it's the operator's own load testing or an actual abuse signal.
- **Hard-stop tier** ($200/day default) — immediate containment per
  `docs/abuse-response.md` §2. Disable the Cognito user pool client or
  detach the API Gateway route, communicate the outage, then
  investigate.

Each alarm's `alarm_actions` and `ok_actions` both publish to the SNS
topic so the operator sees both ALARM transitions and recoveries.

## SNS confirmation flow (operator action required after first apply)

After `terraform apply` provisions the email subscription, AWS sends a
confirmation email to `notification_email` from
`AWS Notifications <no-reply@sns.amazonaws.com>`. The subscription
stays in `PendingConfirmation` state — and the alarms cannot notify —
until the operator clicks the **Confirm subscription** link in that
email.

To verify the subscription is confirmed:

```
aws sns list-subscriptions-by-topic \
  --topic-arn <sns_topic_arn from outputs> \
  --region us-east-1 \
  --query 'Subscriptions[?Protocol==`email`].SubscriptionArn'
```

A confirmed subscription returns an ARN ending in a UUID. An
unconfirmed subscription returns the literal string
`PendingConfirmation`.

If the email is missed, re-trigger by tainting the subscription:

```
terraform taint module.cost_guardrails.aws_sns_topic_subscription.email
terraform apply
```

## Billing-metric lag (6-24 hours)

The `EstimatedCharges` metric updates every 6-24 hours — a hard
physical limit imposed by AWS billing, not configurable. A
catastrophically determined attacker could run up >$1,000 of Bedrock
spend before the alarm fires. The mitigation isn't this module — it's:

1. Per-account Bedrock quotas clamping burst spend within minutes
   (account-level, not configurable here).
2. The synthetic-data property of the demo bounding the *value*
   extracted at zero, even if the cost is non-zero.
3. The runbook's manual-containment options when the alarm does fire.

## Verification (post-apply)

1. Confirm the email subscription per the flow above.
2. Verify the alarms exist and are in `OK` state:

   ```
   aws cloudwatch describe-alarms \
     --alarm-names <warn_alarm_arn alarm_name> <hard_stop_alarm_arn alarm_name> \
     --region us-east-1 \
     --query 'MetricAlarms[].[AlarmName,StateValue]'
   ```

3. (Optional, brutally for-real test) Force a state transition by
   temporarily lowering `warn_threshold_usd` below current Bedrock
   spend and applying. Confirm the email fires. Restore the threshold.
