# Runbook

Operations and decommission procedures. For the happy-path deploy, see `docs/getting-started.md`.

## Incident response

### Symptom: agent returns "I'm not able to share that response"

The Guardrail blocked the output. Check Bedrock-native traces in CloudWatch Logs Insights:

```
fields @timestamp, @message
| filter @message like /guardrailTrace/
| sort @timestamp desc
| limit 50
```

Look at the `action` field. If `INTERVENED`, look at `outputAssessment` for the trigger (PII match, content filter, denied topic). Adjust the Guardrail's `denied_topics` or filter strengths in `terraform/modules/guardrails/variables.tf` and re-apply.

### Symptom: Lambda action group returns 403

Lake Formation denied access. Two common causes:

1. **Persona role's LF-Tag grant is missing.** Check that `aws_lakeformation_permissions` resources for the role exist by inspecting `terraform state list | grep lakeformation_permissions`.
2. **Column tagging didn't apply.** Re-run `./scripts/seed-data.sh` with the `--apply-lf-tags` flag, which is idempotent.

### Symptom: agent asks for clarification instead of calling the tool

Either the system prompt drift or the foundation model changed. The eval `golden.yaml` cases catch this — if a case that previously passed starts failing, look at the `tools_called` field in the report. Adjust `agent_instructions` in `terraform/envs/demo/main.tf` (via the `agent` module variable) and re-apply.

### Symptom: `terraform apply` fails with "data lake settings have admin requirements not met"

The deploying principal must already be a Lake Formation administrator before it can grant LF permissions. Add the principal as an LF admin via the Lake Formation console (or pass it via `lf_admin_principal_arns`) and re-run.

## Rotations

### Rotate Langfuse keys

1. Generate a new public/secret pair in the Langfuse project settings.
2. Update `terraform.tfvars`:
   ```hcl
   langfuse_public_key = "pk-lf-NEW"
   langfuse_secret_key = "sk-lf-NEW"
   ```
3. `terraform apply` — the Secrets Manager entry updates in place.
4. Delete the old keys in Langfuse.

The Lambda and CLI read the secret on each invocation, so no restart required.

### Rotate the Lake Formation admin principal

1. Add the new principal to `lf_admin_principal_arns` in `terraform.tfvars`.
2. `terraform apply`.
3. Verify the new principal can run `aws lakeformation list-permissions` against the database.
4. Remove the old principal from `lf_admin_principal_arns` and re-apply.

Never remove the last LF admin without first adding a new one — there is no escape hatch.

### Rotate the Bedrock foundation model

1. Update `foundation_model_id` in `terraform.tfvars` (e.g., from `anthropic.claude-sonnet-4-6-v1:0` to a newer Sonnet).
2. `terraform apply`. The agent is re-prepared automatically.
3. Run `python eval/runner.py` to confirm golden + red-team cases still pass against the new model. Bedrock Agent behavior across model versions is not byte-stable — expect minor diffs.

## Decommission

### Remove the demo deployment

```bash
cd terraform/envs/demo
terraform destroy
```

S3 buckets are not auto-emptied by `destroy`. Either:

```bash
aws s3 rm s3://$(terraform output -raw data_bucket_name) --recursive
aws s3 rm s3://$(terraform output -raw athena_results_bucket_name 2>/dev/null) --recursive
```

before `destroy`, or empty + delete via the console after.

The Bedrock Guardrail has versions; `destroy` removes the latest version then the guardrail. Custom-defined topics in the Guardrail policy are gone — they live in IaC, not in Bedrock.

### Decommission a per-client deployment (Topology B/C)

Same as above, but the operator runs `terraform destroy` from the client-specific env directory. Send the client a short note with:

- The two S3 buckets that need manual emptying (data + Athena results)
- A reminder that CloudTrail events for the agent persist per their account's CloudTrail retention
- Confirmation that the Langfuse project (cloud-hosted) is unaffected — they can disable it on their side

## CI / OIDC

The CI role is provisioned by `terraform/bootstrap/oidc/` (one-time, separate apply). To rotate the role's permissions, edit `terraform/bootstrap/oidc/main.tf` and `terraform apply` from the bootstrap directory. The `terraform plan` workflow pulls AWS creds via OIDC; no long-lived secrets in GitHub.

If a workflow fails authenticating, check:

1. `secrets.AWS_CI_ROLE_ARN` is set in the GitHub repo's secrets to the bootstrap output's `ci_role_arn`.
2. The `id-token: write` permission is set on the workflow.
3. The OIDC IdP's thumbprint matches the current GitHub-issued cert. AWS rotates this list periodically; if expired, re-run `terraform apply` in `terraform/bootstrap/oidc/` to refresh.

## On-call dashboards (Phase 2)

Not yet built. When ready, add a CloudWatch dashboard pinned to:

- Bedrock Agent invocation count + p95 latency
- Lambda action group error rate
- Guardrail intervention count by type
- Langfuse trace ingest count

Track whatever metric the client cares about most prominently.
