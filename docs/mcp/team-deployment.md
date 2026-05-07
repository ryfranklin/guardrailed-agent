# Team Deployment — Shape B (SSO via IAM Identity Center)

ADR-009 Phase 2.d. The MCP server's "Shape B" deployment shape lets an
internal team adopt the MCP without each developer needing to be a
trusted operator. Persona is bound to the developer's IAM Identity
Center identity, not to a caller-supplied flag.

This doc is written so a team lead can adopt Shape B without follow-up
questions. It assumes you already have:

- An AWS Organization with IAM Identity Center enabled
- A target AWS account where the guardrailed-agent stack is deployed
  (Glue catalog, Bedrock Agent, Lake Formation, persona IAM roles —
  per `terraform/envs/demo/`)
- Developers who use `aws sso login` in their daily workflow

If any of those is missing, start with `docs/getting-started.md` and
the deployment-topology section of `docs/repo-bootstrap-brief.md` first.

---

## Why Shape B exists

Shape A is the personal/consulting demo: one operator, one machine,
`--persona` flag trusted, gated by `GAGENT_TRUSTED_OPERATOR=1`. It works
because the operator owns the trust decision.

Shape B is for teams. Each developer authenticates via IAM Identity
Center (the AWS-side SSO they already use); the MCP server reads their
SSO identity, looks up the persona role, and assumes it with session
tags. **The `--persona` flag is a no-op in Shape B** — it is logged
(WARN) and ignored. This is the safety property: a developer in a
read-only permission set cannot escalate by passing `--persona owner`.

The MCP server is a *transport + interface layer*, not a security
boundary. Shape B inherits the same governance properties Shape A has —
Lake Formation enforces access; Bedrock Guardrails handle prompt
injection; CloudTrail captures every API call — without each team
having to rebuild that plumbing.

---

## Architecture (one picture)

```
                  developer's laptop
   +-----------------------+   +-------------------+
   | Claude Code / Desktop |   | aws sso login     |
   |  (MCP stdio client)   |   | (browser flow)    |
   +----------+------------+   +---------+---------+
              |  spawn                   |
              v                          v
   +-------------------+         IAM Identity Center
   |  python -m        |  <----  permission set: DataAnalyst
   |  mcp_server       |         user: alice@example.com
   |                   |
   |  SsoPersonaResolver
   |   - sts:get_caller_identity
   |   - parse AWSReservedSSO arn
   |   - look up persona_mapping.json
   |   - sts:AssumeRole(persona, session-tags)
   +---------+---------+
             |
             v
   +-------------------+
   | gagent_client     |
   | (Bedrock Agent +  |
   |  Athena under     |
   |  persona creds)   |
   +-------------------+
```

The mapping table is the only deployment-specific configuration.
Everything else — persona role ARNs, agent IDs, Glue database — comes
from the same env vars / terraform outputs the demo uses.

---

## Step 1 — IAM Identity Center configuration

Run these in the AWS Organization's management account.

### 1a. Enable IAM Identity Center

If the Org doesn't have IIC yet:

```
AWS Console -> IAM Identity Center -> Enable
```

Pick a region close to most users; AWS replicates IIC globally.

### 1b. Create permission sets

You need at least three permission sets, one per persona. The names you
choose go into the `persona_mapping.json` file; they don't need to match
exactly but the convention `GuardrailedAgent<Persona>` makes the wiring
obvious.

| Permission set | What it grants on the *developer's* laptop |
|---|---|
| `GuardrailedAgentDispatcher` | `sts:AssumeRole` on the `gagent-dispatcher-<env>` role only |
| `GuardrailedAgentTechnicianLead` | `sts:AssumeRole` on the `gagent-technician-lead-<env>` role only |
| `GuardrailedAgentOwner` | `sts:AssumeRole` on the `gagent-owner-<env>` role only |

The permission-set inline policy only needs `sts:AssumeRole` on the
matching persona ARN — the developer's IIC session is *not* the role
LF evaluates against. The persona role's trust policy gates the actual
assumption (next step).

Example inline policy for `GuardrailedAgentDispatcher`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["sts:AssumeRole", "sts:TagSession"],
      "Resource": "arn:aws:iam::<TARGET_ACCOUNT>:role/gagent-dispatcher-<env>"
    }
  ]
}
```

### 1c. Assign permission sets to users / groups

In the IIC console:

```
Multi-account permissions -> AWS accounts -> <target account>
  -> Assign users or groups -> pick the IIC group -> attach permission set
```

The typical pattern is:

- One IIC group per role (`gagent-data-readers`, `gagent-data-leads`,
  `gagent-data-admins`)
- Each group assigned the matching permission set on the target account

That's it for IAM Identity Center. Developers can now `aws sso login`
and assume the right permission set.

---

## Step 2 — Persona role provisioning (terraform)

The persona roles already exist if `terraform apply` has been run for
the env. Their trust policies trust the deploying caller's ARN
(`gagent-admin` or similar). For Shape B, you need to add the IIC
permission-set assumed-role principal to each persona's trust policy.

In `terraform/envs/<env>/terraform.tfvars`:

```hcl
trusted_assumer_arns = [
  # IIC permission-set assumed-role ARN format:
  # arn:aws:iam::<account>:role/aws-reserved/sso.amazonaws.com/<region>/AWSReservedSSO_<permission_set>_<hash>
  "arn:aws:iam::<TARGET_ACCOUNT>:role/aws-reserved/sso.amazonaws.com/us-east-1/AWSReservedSSO_GuardrailedAgentDispatcher_*",
  "arn:aws:iam::<TARGET_ACCOUNT>:role/aws-reserved/sso.amazonaws.com/us-east-1/AWSReservedSSO_GuardrailedAgentTechnicianLead_*",
  "arn:aws:iam::<TARGET_ACCOUNT>:role/aws-reserved/sso.amazonaws.com/us-east-1/AWSReservedSSO_GuardrailedAgentOwner_*",
]
```

The wildcard `*` covers the IIC-generated suffix on the role name.
After `terraform apply`, the persona roles will trust the IIC-assumed-role
principals; developers can `sts:AssumeRole` on them.

> **Important:** the persona role's trust policy still requires the
> session tag `role=<persona>` and (for technician_lead) `service_region`.
> The MCP server adds those tags automatically; developers don't have to.

---

## Step 3 — Per-team adoption checklist

What each developer does on their machine. Roughly 10 minutes if AWS
SSO is already configured.

### 3a. Clone the repo + install

```bash
git clone https://github.com/<org>/guardrailed-agent.git
cd guardrailed-agent
python3.12 -m venv .venv
.venv/bin/pip install -e .
```

### 3b. Write `persona_mapping.json` for your team

Copy the shipped default and customize:

```bash
cp gagent_client/persona_mapping.json ./team_mapping.json
$EDITOR ./team_mapping.json
```

Replace the example permission-set names with the ones from Step 1b:

```json
{
  "version": 1,
  "default_persona": null,
  "default_service_region": null,
  "rules": [
    {"match": {"permission_set": "GuardrailedAgentDispatcher"},
     "persona": "dispatcher"},
    {"match": {"permission_set": "GuardrailedAgentTechnicianLead"},
     "persona": "technician_lead",
     "service_region": "tempe-mesa"},
    {"match": {"permission_set": "GuardrailedAgentOwner"},
     "persona": "owner"}
  ]
}
```

User-specific overrides take precedence over permission-set rules:

```json
{"match": {"sso_user_id": "alice@example.com"}, "persona": "owner"}
```

### 3c. Configure AWS SSO

```bash
aws configure sso
# follow the browser flow; pick the right start URL + region
aws sso login
# verify:
aws sts get-caller-identity
# Arn must look like:
# arn:aws:sts::123:assumed-role/AWSReservedSSO_GuardrailedAgentDispatcher_<hash>/alice@example.com
```

### 3d. Set environment variables

The MCP server needs the persona role ARNs + agent IDs + the mapping
file path. Operations teams often ship these in a per-team config file
(`.envrc` for direnv, or a `.env` file for dotenv loaders).

```bash
export GAGENT_PERSONA_MAPPING_FILE="$PWD/team_mapping.json"
export GAGENT_DISPATCHER_ROLE_ARN="arn:aws:iam::<account>:role/gagent-dispatcher-<env>"
export GAGENT_TECHNICIAN_LEAD_ROLE_ARN="arn:aws:iam::<account>:role/gagent-technician-lead-<env>"
export GAGENT_OWNER_ROLE_ARN="arn:aws:iam::<account>:role/gagent-owner-<env>"
export GAGENT_AGENT_ID="<from terraform output>"
export GAGENT_AGENT_ALIAS_ID="<from terraform output>"
export GAGENT_GLUE_DATABASE="guardrailed_agent_<env>"
export GAGENT_ATHENA_WORKGROUP="gagent-<env>"
export AWS_REGION="us-east-1"
# DO NOT set GAGENT_TRUSTED_OPERATOR — Shape B mode requires it unset
```

### 3e. Smoke test

```bash
python scripts/smoke_mcp.py
```

Expected output:

```
spawning: ... -m mcp_server
=== initialize ===
server: gagent-mcp v0.1.0 shape=B
=== call: list_tools ===
{
  "default_persona": "dispatcher",   # <-- bound to your IIC permission set
  "available_personas": ["dispatcher"],
  ...
}
```

### 3f. Register with Claude Code

Add a `gagent` MCP entry to `~/.claude/mcp.json` (per
`docs/mcp/claude_code_config.json.example`). The Shape A example file
sets `GAGENT_TRUSTED_OPERATOR=1` — **omit that env var for Shape B**:

```json
{
  "mcpServers": {
    "gagent": {
      "command": "/abs/path/to/.venv/bin/python",
      "args": ["-m", "mcp_server"],
      "cwd": "/abs/path/to/guardrailed-agent",
      "env": {
        "GAGENT_PERSONA_MAPPING_FILE": "/abs/path/to/team_mapping.json",
        "GAGENT_DISPATCHER_ROLE_ARN": "...",
        "GAGENT_TECHNICIAN_LEAD_ROLE_ARN": "...",
        "GAGENT_OWNER_ROLE_ARN": "...",
        "GAGENT_AGENT_ID": "...",
        "GAGENT_AGENT_ALIAS_ID": "...",
        "GAGENT_GLUE_DATABASE": "...",
        "GAGENT_ATHENA_WORKGROUP": "...",
        "AWS_REGION": "us-east-1"
      }
    }
  }
}
```

Restart Claude Code. The `gagent` server should appear in the MCP
status panel; ask "what tables can I see?" to verify.

---

## Step 4 — Boundaries note (the part you'll be asked about)

> **The MCP server enforces nothing. Lake Formation and Bedrock
> Guardrails enforce everything.**

When a security or platform reviewer asks "is this safe to give to a
team?", point them at this:

- **Authentication.** The developer authenticates to AWS via IAM
  Identity Center. The MCP server reads `sts:GetCallerIdentity` to learn
  who they are; it does not invent or vouch for identity.
- **Authorization (data access).** The MCP server's role is
  `sts:AssumeRole` on a persona. Lake Formation evaluates the
  `aws:PrincipalTag/role` (and `service_region`) on the assumed role at
  query time. **The MCP server cannot bypass LF.** If a developer's
  persona is Dispatcher, every query they cause to run hits LF as
  Dispatcher.
- **Authorization (model interaction).** Bedrock Guardrails are
  attached to the agent definition itself, not the MCP server. PII
  filters, prompt-attack defense, denied topics — all enforced
  upstream of the MCP transport.
- **`--persona` override.** The flag is honored only in Shape A
  (`GAGENT_TRUSTED_OPERATOR=1`). In Shape B the `SsoPersonaResolver`
  ignores the override and logs a WARN — *the persona is bound to
  the SSO identity*. A developer cannot escalate by passing
  `--persona owner` to a tool call.
- **Audit.** Every assumed-role call is in CloudTrail with the
  developer's IIC identity. Every Lake Formation `GetDataAccess` is in
  CloudTrail. Every Bedrock invocation is in CloudWatch (Bedrock
  auto-trace + the gagent invocation log group). See `audit_trace`
  (Tool 6) for one-shot correlation.

What the MCP server *does* enforce: rate-limit warnings, token-budget
warnings, structured logging. None of that is security-critical — if
the MCP has a bug, the worst case is an operability incident, not a
data leak. That property is what makes it safe to recommend to teams
the operator does not control.

---

## Troubleshooting

### `sts:GetCallerIdentity failed: ExpiredToken`

Your SSO session timed out. Run `aws sso login` again.

### `Caller identity ... is not an IAM Identity Center assumed-role`

You're authenticated as a long-term IAM user instead of via SSO. Either:
- Switch your AWS profile to one configured for SSO (`aws configure sso`).
- Or set `GAGENT_TRUSTED_OPERATOR=1` to use Shape A (single-operator
  mode) — but that doesn't deliver the Shape B safety properties, so
  only do this for personal experimentation.

### `No persona mapping for SSO identity (permission_set='X', sso_user_id='you@example.com')`

Your IIC permission set isn't in `persona_mapping.json`. Add a rule:

```json
{"match": {"permission_set": "X"}, "persona": "dispatcher"}
```

…or set `default_persona`.

### Persona role assumption fails with `AccessDenied`

The persona role's trust policy doesn't permit your IIC permission-set
ARN. Re-check Step 2 (terraform `trusted_assumer_arns`) and confirm
`terraform apply` ran. The trust policy must include the
`AWSReservedSSO_<your_permission_set>_*` wildcard ARN.

### `ignoring role='owner' override` warning in MCP logs

You (or Claude) are passing `--persona owner` in a tool call. In
Shape B that argument is dropped — the persona is bound to your SSO
identity. This is intentional. If you legitimately need a different
persona, get assigned to the matching IIC permission set.

---

## What changes in Phase 3

Phase 3 (per the README roadmap; not yet ADR'd) introduces *external*
identity for the Slack adapter and the Vercel/Cognito web demo. The
patterns are additive:

- The `gagent_client/identity.py` interface gains a `CognitoPersonaResolver`
  alongside `FlagPersonaResolver` and `SsoPersonaResolver`.
- The Slack adapter and Vercel UI consume `gagent_client/`, not the MCP
  server. They reuse the persona-resolution interface beneath the surface;
  the MCP server is one of three transports on top.
- No tool defined for Phase 2 will be renamed, deprecated, or migrated.

In other words: nothing on this page will change for Phase 3. Adopt
Shape B with confidence.

---

## References

- [ADR-009 — MCP server as reference implementation](../../consulting/guardrailed-agent/decisions/009-mcp-as-reference-implementation.md)
- [ADR-006 — Phase 2 personal interaction surfaces](../../consulting/guardrailed-agent/decisions/006-personal-interaction-surfaces.md)
- [ADR-003 — Data plane and identity (ABAC propagation chain)](../../consulting/guardrailed-agent/decisions/003-data-plane-and-identity.md)
- `gagent_client/identity.py` — `SsoPersonaResolver` source
- `gagent_client/persona_mapping.json` — default mapping; copy + customize
- `docs/mcp/claude_code_config.json.example` — Shape A reference config
- `docs/mcp/governance-tools.md` — `explain_governance` / `eval_query` /
  `audit_trace` (the killer tools that pair with Shape B adoption)
