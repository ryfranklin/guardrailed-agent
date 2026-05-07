# Operator Runbook

The single source of truth for **how to run the demo cold**. Everything
on this page is copy-pasteable; deeper writeups live in the linked
docs. Targets two users:

- **First-time operator** going from "fresh AWS account" to "demo
  screen-share ready" (~30 min).
- **Returning operator** sitting down to demo on a deployed env, who
  needs to verify health before opening the talk track (~2 min).

If you're between deploys, jump to **§5 — Pre-demo verification**.

---

## 0. Prerequisites

| Requirement | How to satisfy |
|---|---|
| Terraform `>= 1.7` | `brew install terraform` / `tfenv install latest` |
| Python `3.12` | `brew install python@3.12` |
| AWS CLI v2 + admin creds in target account | `aws configure sso` (or `~/.aws/credentials` profile) |
| Bedrock model access for `anthropic.claude-sonnet-4-6` | Bedrock console → Model access → Request |
| CloudWatch Logs access in the target account | Native to AWS — no extra account or vendor needed; AgentCore Observability surfaces the `/gagent/invocations` log group automatically. |

Set `AWS_PROFILE` once and forget it: every command in this runbook
honors it. The default in this repo is `AWS_PROFILE=ms3dm-admin`.

---

## 1. One-time bootstrap (per AWS account)

```bash
git clone <this repo> && cd guardrailed-agent
cp terraform/envs/demo/terraform.tfvars.example terraform/envs/demo/terraform.tfvars
$EDITOR terraform/envs/demo/terraform.tfvars     # tweak settings as needed (defaults are usually fine)

cd terraform/envs/demo
AWS_PROFILE=ms3dm-admin terraform init
AWS_PROFILE=ms3dm-admin terraform apply
```

**You're done when** `terraform output` returns the persona role ARNs,
agent ID, alias ID, glue database, and invocation log group name — the
values every later step reads.

> The Bedrock provider has a known cosmetic bug where the agent's
> `guardrail_configuration` may show `null` after the first apply.
> Re-run `terraform apply` to converge state. See
> `docs/getting-started.md` for the longer write-up.

---

## 2. Seed the dataset (after every clean deploy or schema change)

```bash
AWS_PROFILE=ms3dm-admin ./scripts/seed-data.sh
```

That script runs end-to-end:
1. Generates 10 parquet files (core + supporting tables).
2. Generates `equipment_telemetry_daily` + `technician_utilization_daily`.
3. Uploads everything to `s3://gagent-data-demo-<account>/staging/`.
4. CTAS each of the 12 tables into Iceberg via Athena.
5. Applies the dual LF-tag scheme (250 attachments).

**You're done when** the script's last lines say
`Seed pipeline complete.` and the verify command at the bottom returns
`summary {"mismatch": 0, "missing": 0, "ok": 250, "table_missing": 0}`.

---

## 3. Install local tooling

The MCP server, the `gra` CLI, and the eval/integration tests all run
from this venv:

```bash
data/synthesizer/.venv/bin/pip install -e .
```

That installs three console scripts on PATH (inside the venv):
`gra`, plus the Python packages `gagent_client` / `mcp_server` / `gra`.

Quick check:

```bash
data/synthesizer/.venv/bin/gra --help        # CLI
data/synthesizer/.venv/bin/python -m mcp_server  # would start the MCP
                                                 # server; Ctrl-C to quit
```

---

## 4. Set environment variables (every shell session)

These are the same envs every surface (gra CLI, MCP server, notebook,
smoke scripts) reads. Source them once per shell:

```bash
export AWS_PROFILE=ms3dm-admin
export AWS_REGION=us-east-1
export GAGENT_TRUSTED_OPERATOR=1                    # Shape A — single operator
export GAGENT_DISPATCHER_ROLE_ARN=$(terraform -chdir=terraform/envs/demo output -raw dispatcher_role_arn)
export GAGENT_TECHNICIAN_LEAD_ROLE_ARN=$(terraform -chdir=terraform/envs/demo output -raw technician_lead_role_arn)
export GAGENT_OWNER_ROLE_ARN=$(terraform -chdir=terraform/envs/demo output -raw owner_role_arn)
export GAGENT_AGENT_ID=$(terraform -chdir=terraform/envs/demo output -raw agent_id)
export GAGENT_AGENT_ALIAS_ID=$(terraform -chdir=terraform/envs/demo output -raw agent_alias_id)
export GAGENT_GLUE_DATABASE=$(terraform -chdir=terraform/envs/demo output -raw glue_database_name)
export GAGENT_ATHENA_WORKGROUP=$(terraform -chdir=terraform/envs/demo output -raw athena_workgroup_name)
export GAGENT_FOUNDATION_MODEL_ID=us.anthropic.claude-sonnet-4-6
```

> For team adoption (Shape B), do *not* set
> `GAGENT_TRUSTED_OPERATOR`. See `docs/mcp/team-deployment.md`.

---

## 5. Pre-demo verification (the 2-minute health check)

Run these three commands top-to-bottom. They're the "demo readiness"
gate. If any fails, fix before opening the talk track.

```bash
# 5a. Agent path: PII redaction across personas (~15 sec)
./scripts/smoke-test.sh

# 5b. Direct Athena path: sensitivity tag enforcement (~10 sec)
./scripts/smoke-test-sensitivity.sh

# 5c. MCP path: all 9 tools reachable via stdio (~30 sec)
data/synthesizer/.venv/bin/python scripts/smoke_mcp.py
```

**You're ready when:**

- 5a's *Dispatcher* response contains `REDACTED` or null PII fields;
  *Owner* response contains real PII.
- 5b's output is `dispatcher: FAILED ... Owner: SUCCEEDED with five rows`.
- 5c ends with `smoke test complete.` and exit 0.

If you have time, also:

```bash
data/synthesizer/.venv/bin/gra personas    # confirms env + ARNs
data/synthesizer/.venv/bin/gra traces --limit 3   # confirms CloudWatch Logs Insights query path
```

---

## 6. Demo paths — pick the one that fits the audience

### 6a. The 90-second talk track (general audience)

The canonical demo. Open three terminals or browser tabs:

1. Terminal — `./scripts/smoke-test.sh` (already verified in §5a).
2. Browser — CloudWatch Logs Insights for the `/gagent/invocations`
   log group (or the GenAI Observability console view), filtered to
   the last hour:
   `https://console.aws.amazon.com/cloudwatch/home?region=<region>#logsV2:log-groups/log-group/$252Fgagent$252Finvocations`
3. Browser — CloudTrail event search, `eventName = InvokeAgent`.

Run the smoke. Walk the three windows. Talk track + question prep:
**`docs/demo-script.md`** (Dispatcher vs Owner narrative; "what stops a
prompt-injection bypass?"; "what if the dispatcher is curious about
another region?").

### 6b. The MCP-in-Claude-Code demo (developer / IDE audience)

Install the MCP server in Claude Code:

```bash
$EDITOR ~/.claude/mcp.json   # paste from docs/mcp/claude_code_config.json.example
```

Adjust the absolute paths and AWS_PROFILE in that config. Restart
Claude Code. In a chat:

> "What tables can I see?"

Claude calls `describe_schema`; 12 tables come back. Then:

> "Use explain_governance to show me which columns dispatcher would see
> on the customer table, then call eval_query for `SELECT * FROM
> customer LIMIT 100`."

This is the killer-tool moment per ADR-009 — `explain_governance` +
`eval_query` answer "what does the policy say?" and "how much will it
cost?" without a data engineer in the loop. Reference:
**`docs/mcp/governance-tools.md`** (live JSON samples for the slides).

### 6c. The notebook walkthrough (analyst / chart-driven audience)

Two surfaces:

- **Static link**, ready to share in a Substack or email:
  `docs/notebooks/reference.html`. Self-contained, 424 KB, two charts,
  three personas, one ML-read example.
- **Live in SMUS or local Jupyter**: `notebooks/reference.ipynb`. Set
  the same env vars from §4, then `jupyter lab notebooks/`. The
  notebook auto-detects its parent dir and imports `gagent_client`
  without manual edits.

---

## 7. Recovery — when things break

| Symptom | Fix |
|---|---|
| `terraform apply` shows `guardrail_configuration: null` and bails | Re-run `terraform apply`; second pass converges. Provider bug. |
| `register-iceberg.py` fails with `ICEBERG_TOO_MANY_OPEN_PARTITIONS` | Already fixed in repo (month-partition transform). If it returns, lower partition cardinality further or drop partitioning. |
| Smoke test 5a says "access denied" for both personas | LF tags drifted. Run `data/synthesizer/.venv/bin/python scripts/apply-lf-tags.py --database $GAGENT_GLUE_DATABASE --verify` — fix any `mismatch` / `missing`. |
| `gra` / MCP returns `agent_id not configured` | §4 envs are missing or stale. Re-source. |
| Traces don't appear | Check that the operator/MCP role has `logs:PutLogEvents` on `/gagent/invocations`. The emission is best-effort and `gra traces --limit 5` exits 3 with the underlying error. |
| `Bedrock agent description "stale"` after a rename | The agent alias is replaced on apply; re-read `agent_alias_id` from terraform output. The `gra` CLI and MCP do this automatically; only hand-coded scripts break. |

For deeper diagnostics, the `audit_trace` MCP tool correlates an
invocation `session_id` with the matching CloudTrail events for the
same window — the one-shot "what actually happened?" tool.

---

## 8. Teardown

```bash
cd terraform/envs/demo
AWS_PROFILE=ms3dm-admin terraform destroy
```

S3 buckets are retained by design (the data bucket holds Iceberg
metadata + partitioned parquet; the athena-results bucket has a 30-day
lifecycle). Empty + delete them manually if a clean teardown is
required:

```bash
aws s3 rm --recursive s3://gagent-data-demo-<account>/
aws s3 rm --recursive s3://gagent-athena-demo-<account>/
aws s3 rb s3://gagent-data-demo-<account>/
aws s3 rb s3://gagent-athena-demo-<account>/
```

---

## 9. What lives where (single-page index)

| You want… | Open this |
|---|---|
| The 90-sec demo talk track | `docs/demo-script.md` |
| Per-step deploy walk-through | `docs/getting-started.md` |
| The phase 2.b governance-tools reference | `docs/mcp/governance-tools.md` |
| Team adoption (Shape B SSO) | `docs/mcp/team-deployment.md` |
| Claude Code / Desktop config templates | `docs/mcp/claude_code_config.json.example` / `claude_desktop_config.json.example` |
| The ADRs (architecture decisions) | `consulting/guardrailed-agent/decisions/*.md` (ms3dm.tech vault) |
| A shareable HTML notebook | `docs/notebooks/reference.html` |
| The runnable notebook | `notebooks/reference.ipynb` |
| Repo bootstrap rationale | `docs/repo-bootstrap-brief.md` |
| Architecture deep-dive | `consulting/guardrailed-agent/architecture/data-and-governance.md` (vault) |
