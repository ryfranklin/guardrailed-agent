# MCP Governance Tools — explain_governance, eval_query, audit_trace

ADR-009 Phase 2.b. Three read-only tools wrapped on top of `gagent_client`
that close the loop between a user asking *"why didn't I see column X?"*
and an answerable, auditable response. None of them executes the user's
query; all of them probe the catalog + policy + observability surface.

## Why these three

The MVP tools (`ask_agent`, `describe_schema`, `list_tools`) ship a
working agent in Claude Code. They don't yet differentiate this MCP from
*any other SQL-over-MCP server*. The Phase 2.b additions do:

| Tool | What it answers |
|---|---|
| **explain_governance** | "Why would Lake Formation hide column X for persona Y *if* I ran this query?" |
| **eval_query** | "Will this query be expensive, and which grants apply?" |
| **audit_trace** | "Show me everything that happened during invocation T." |

These are the questions enterprise security and platform teams ask. The
MCP answers them without anyone DMing a data engineer.

## Tool 1 — `explain_governance(query, persona, service_region?)`

Static probe — does **not** execute the query. Walks every table
referenced in the SQL, fetches column LF tags + the persona's
tag-policy grants, and reports redacted columns + the grant evidence
that explains the redaction.

### Inputs

| Parameter | Required | Description |
|---|---|---|
| `query` | ✓ | Athena SQL. The probe extracts FROM/JOIN tables and ignores everything else. |
| `persona` | — | `dispatcher` / `technician_lead` / `owner`. Defaults to the server's `GAGENT_DEFAULT_PERSONA`. |
| `service_region` | — | Required when `persona=technician_lead`. |

### Output shape

```json
{
  "query": "SELECT * FROM customer LIMIT 5",
  "persona": "dispatcher",
  "service_region": null,
  "tables_referenced": ["customer"],
  "redacted_columns": [
    {
      "table": "customer",
      "column": "first_name",
      "tags": {"pii": "true", "sensitivity": "low"},
      "reason": "pii=true not in ['false']"
    },
    {
      "table": "customer",
      "column": "billing_notes",
      "tags": {"pii": "true", "sensitivity": "medium"},
      "reason": "pii=true not in ['false']"
    }
  ],
  "visible_columns": [
    {
      "table": "customer",
      "column": "customer_id",
      "tags": {"pii": "false", "sensitivity": "other"},
      "reason": "matched sensitivity=['other'] AND pii=['false']"
    }
  ],
  "row_filters": [],
  "grant_evidence": [
    {
      "permissions": ["DESCRIBE", "SELECT"],
      "permissions_with_grant_option": [],
      "resource_type": "TABLE",
      "tag_expression": [
        {"key": "sensitivity", "values": ["other"]},
        {"key": "pii", "values": ["false"]}
      ],
      "tag_expression_str": "sensitivity=['other'] AND pii=['false']"
    }
  ],
  "table_reports": [
    {
      "table": "customer",
      "column_count": 15,
      "redacted": ["first_name", "last_name", "email", "phone",
                   "street_address", "city", "postal_code", "billing_notes"],
      "visible": ["customer_id", "customer_type", "service_tier",
                  "service_region", "effective_from", "effective_to",
                  "is_current"]
    }
  ]
}
```

### What `row_filters` means (and currently doesn't)

The Phase 2 LF row-filter (per-region scoping for TechnicianLead) lives
in ADR-003 open items. Until that lands, `row_filters` is always `[]` —
the field exists so the response shape is stable for clients now, and
won't change when the row-filter feature ships.

### Acceptance proof

`tests/integration/test_governance_diff.py` runs `explain_governance`
for each persona on a `customer × equipment × service_job` join, then
runs the same query through Athena under the persona's STS credentials,
and diffs the columns Athena hid against the predictions. The CI
workflow (`.github/workflows/eval.yml`) runs this on the schedule + on
manual dispatch (live AWS spend).

## Tool 2 — `eval_query(query, persona, service_region?, usd_per_tb?)`

Pre-flight cost + grant report. Estimates scanned bytes from Glue
parameters (with an S3 ListObjects fallback for Iceberg tables that
lack populated stats), projects USD cost at the workgroup's per-TB
rate, and includes the persona's effective grant set.

The query is **not** executed.

### Inputs

| Parameter | Required | Description |
|---|---|---|
| `query` | ✓ | Athena SQL. |
| `persona` | — | Persona for grant resolution. |
| `service_region` | — | Required when `persona=technician_lead`. |
| `usd_per_tb` | — | Override the Athena per-TB price (default `5.00` USD, us-east-1). |

### Output shape

```json
{
  "query": "SELECT * FROM customer LIMIT 5",
  "persona": "owner",
  "service_region": null,
  "tables_referenced": ["customer"],
  "table_stats": [
    {
      "table": "customer",
      "column_count": 15,
      "size_bytes_estimate": 309516,
      "size_human": "302.26 KB",
      "row_count_estimate": null
    }
  ],
  "scanned_bytes_estimate": 309516,
  "scanned_bytes_human": "302.26 KB",
  "cost_estimate_usd": 1e-06,
  "cost_per_tb_usd": 5.0,
  "bytes_per_tb": 1099511627776,
  "grant_set": [
    {
      "permissions": ["DESCRIBE", "SELECT"],
      "permissions_with_grant_option": [],
      "resource_type": "TABLE",
      "tag_expression": [
        {"key": "pii", "values": ["false", "true"]},
        {"key": "sensitivity", "values": ["high", "other"]}
      ],
      "tag_expression_str": "pii=['false', 'true'] AND sensitivity=['high', 'other']"
    }
  ],
  "warnings": [],
  "notes": [
    "Estimate is a static upper bound from Glue table parameters. Actual scan is typically smaller because Iceberg + Athena prune files by filter / projection. Compare to QueryExecution.Statistics.DataScannedInBytes after running."
  ]
}
```

### Accuracy

The estimate is an **upper bound**. We sum every object under the
table's S3 prefix; Athena typically scans less because:

- Iceberg manifest files are pruned by predicate / partition.
- Column projection (Parquet) reads only the columns selected.
- LIMIT pushes down for some queries.

For full-scan queries (`SELECT *` no filter), the estimate lands within
~10% of `QueryExecution.Statistics.DataScannedInBytes`. For aggressive
projections or partition-pruned queries, the estimate can be 10× too
high. The `notes` field tells callers to compare against the post-run
number for ground truth.

## Tool 3 — `audit_trace(session_id, window_minutes?, lookback_hours?)`

Given an invocation `session_id`, looks up the matching CloudWatch
invocation log entry (via Logs Insights against `/gagent/invocations`)
and the CloudTrail events that fired in the same time window. Returns
the trace + events grouped by `EventName`. The legacy `trace_id`
parameter name is accepted as a deprecated alias for `session_id`.

### How correlation works

`gagent_client.invoke` writes the STS `RoleSessionName` into the
invocation log entry (alongside persona, role_arn, session_id).
CloudTrail logs every assumed-role API call under the session name.
`audit_trace` reads the session name from the log entry and uses it as
a `Username` filter on `cloudtrail:LookupEvents`.

### Inputs

| Parameter | Required | Description |
|---|---|---|
| `session_id` | ✓ | Invocation session_id (the value `gagent_client.invoke` returns and writes into the `/gagent/invocations` log group). `trace_id` is accepted as a deprecated alias. |
| `window_minutes` | — | Minutes after the invocation's start time to scan in CloudTrail (default 5). |
| `lookback_hours` | — | How far back in the invocation log group to search for the session_id (default 168, i.e. 7 days). |

### Output shape

```json
{
  "trace_id": "trace-abc-123",
  "trace": {
    "id": "trace-abc-123",
    "name": "mcp-ask-owner",
    "timestamp": "2026-05-03T18:01:00+00:00",
    "input": "Show me customer 32869c51's contact info",
    "output_preview": "Here is the customer record..."
  },
  "persona": "owner",
  "role_arn": "arn:aws:iam::608050308596:role/gagent-owner-demo",
  "role_session_name": "gagent-owner-abc123",
  "window": {
    "start": "2026-05-03T18:00:30+00:00",
    "end": "2026-05-03T18:06:00+00:00",
    "minutes": 5
  },
  "cloudtrail_event_count": 4,
  "events_by_name": {
    "AssumeRole": [
      {"EventId": "...", "EventName": "AssumeRole", "EventTime": "...",
       "Username": "ms3dm-admin", "EventSource": "sts.amazonaws.com",
       "Resources": []}
    ],
    "StartQueryExecution": [
      {"EventId": "...", "EventName": "StartQueryExecution", "EventTime": "...",
       "Username": "gagent-owner-abc123", "EventSource": "athena.amazonaws.com",
       "Resources": []}
    ],
    "GetDataAccess": [
      {"EventId": "...", "EventName": "GetDataAccess", "EventTime": "...",
       "Username": "gagent-owner-abc123",
       "EventSource": "lakeformation.amazonaws.com",
       "Resources": [{"ResourceType": "AWS::Glue::Table", "ResourceName": "..."}]}
    ]
  }
}
```

CloudTrail's 90-day default retention bounds how far back this works.
Traces older than 90 days return `cloudtrail_event_count: 0` unless
the account has an extended-retention Trail.

## IAM permissions

The probe is read-only against the catalog, the policy, CloudTrail, and
the data bucket. The `terraform/envs/demo/main.tf` module creates a
managed policy at:

```
output: mcp_governance_reader_policy_arn
```

Attach it to whichever principal runs the MCP server. For Shape A
(personal demo) the operator typically already has admin permissions —
the policy exists for client deployments where the operator is *not*
admin, and for the Phase 2.d Shape B server-side role.

The policy includes:

| Action | Why |
|---|---|
| `glue:GetTable`, `GetTables`, `GetColumnStatisticsForTable` | Column lists + Iceberg stats |
| `lakeformation:GetResourceLFTags` | Per-column tag attachments |
| `lakeformation:ListPermissions` | Persona's tag-policy grants |
| `lakeformation:ListLFTags`, `GetLFTag` | Tag definitions for diagnostics |
| `cloudtrail:LookupEvents` | `audit_trace` correlation |
| `s3:ListBucket`, `GetBucketLocation` | Iceberg size fallback |

## Try them in Claude Code

With the MCP server registered (per `docs/mcp/claude_code_config.json.example`):

> **You:** Use explain_governance to predict what columns dispatcher would see when running `SELECT * FROM customer JOIN service_job ON service_job.customer_id = customer.customer_id LIMIT 5`. Then call eval_query for the same query and persona.

> **Claude:** [calls `explain_governance` then `eval_query`, summarizes redacted columns + cost]

The two-step pattern — explain, then evaluate — is the human-in-the-loop
review story enterprise teams will want for sensitive queries: agent drafts,
human reviews the governance + cost report, agent (or human) executes.

## What's next (Phase 2.c, Prompt 2.6)

Three more tools after this:
- `propose_query` — agent drafts SQL without executing.
- `recent_traces` — recent invocations by persona.
- `health` — Bedrock + Athena + Glue + LF reachability.

Then Phase 2.d (Prompt 2.7): Shape B SSO resolver for team adoption.

## References

- [ADR-009 — MCP server as reference implementation](../../consulting/guardrailed-agent/decisions/009-mcp-as-reference-implementation.md)
- [ADR-003 — Data plane and identity](../../consulting/guardrailed-agent/decisions/003-data-plane-and-identity.md)
- [ADR-008 — HVAC schema + dual-tag scheme](../../consulting/guardrailed-agent/decisions/008-dataset-pivot-hvac-home-services.md)
- `tests/integration/test_governance_diff.py` — the acceptance test
- `terraform/envs/demo/main.tf` — `mcp_governance_reader_policy_arn`
