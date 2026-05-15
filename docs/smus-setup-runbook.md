# SMUS setup runbook — happy path

End-to-end recipe for setting up a SageMaker Unified Studio domain and a
user project that can both consume existing Glue catalog data (e.g. the
demo's `guardrailed_agent_demo`) and provision new S3 Tables catalogs.

Distilled from the painful first pass on 2026-05-14. This runbook
captures the **shortest path that works**; it skips the dead ends.

## Relationship to AWS official docs

AWS publishes two pages that describe the canonical S3 Tables + SMUS
flow:

- [Amazon S3 Tables integration](https://docs.aws.amazon.com/sagemaker-lakehouse-architecture/latest/userguide/lakehouse-s3-tables-integration.html)
- [Working with Amazon S3 Tables in the lakehouse architecture of Amazon SageMaker](https://docs.aws.amazon.com/sagemaker-lakehouse-architecture/latest/userguide/s3-tables-integration.html)

**Those docs describe the intended setup.** They cover: enabling
S3 ↔ Glue integration (one-time, per region), granting Lake Formation
Super User on the table bucket to the SMUS project role, and creating
tables via Athena DDL.

**This runbook covers what the AWS docs don't:** three IAM patches
required for the canonical setup to actually work in accounts where
the AWS-managed policies on the SMUS roles
(`AmazonSageMakerProvisioning-<account>` and
`datazone_usr_role_<project>_<env>`) haven't yet been updated by AWS
to include `s3tables:*` and `glue:Get*` on the s3tables sub-catalogs.
Those gaps surface as the recurring 403 denials documented in the
Common pitfalls table at the bottom.

If a future revision of those AWS-managed policies includes the
missing actions, Phases 1 and 6 of this runbook become unnecessary
and the AWS docs' steps will work as written. Until then, do the
patches.

## Prerequisites

- AWS account `608050308596` (substitute as needed)
- IAM Identity Center already enabled and configured for the account
- An admin-level identity in Identity Center (e.g. `ms3dm-admin`)
- AWS CLI configured to that identity, `us-east-1` region
- Account is the LF data lake admin

## Decisions baked into this runbook

| Setting | Value | Why |
|---|---|---|
| SMUS auth mode | **IAM Identity Center** | IAM-only mode hides DataZone features (blueprints, project profiles, asset publishing). Identity Center unlocks the full feature set. |
| Project profile for user work | **All capabilities** | Includes Lakehouse Catalog, Tooling, SQL Analytics — enough for the demo + room to grow. |
| Glue catalog import | `bring-your-own-gdc-assets` migration script | Single command handles LF opt-in + grants + asset registration. |
| S3 Tables creation | **CLI / Terraform**, not SMUS UI | SMUS UI's table creation is Glue-only; S3 Tables go through `aws s3tables` and Athena DDL. |
| S3 Tables querying | Athena Query Editor with **DataZone environment** selected | Inherits project session context; satisfies LF conditional grants. |

---

## Phase 0 — Create the SMUS domain (one-time per account)

Use the SageMaker console → Unified Studio → Create domain.

Required:
- **Authentication: IAM Identity Center**. Do not pick IAM-only — that
  hides every workflow we'll need later.
- Add `ms3dm-admin` as a domain administrator.
- Accept the AWS-managed default IAM roles
  (`AmazonSageMakerDomainExecutionRole_<...>`,
  `AmazonSageMakerProvisioning-<account>`, etc.).

After creation, note the domain ID (looks like `dzd-<hash>`). Substitute
into the commands below.

## Phase 1 — Apply the one-time S3 Tables IAM patch

**Not in AWS docs.** The SMUS provisioning role's AWS-managed policy
doesn't include `s3tables:*` permissions. Without this, every Lakehouse
Catalog provisioning will fail with `AccessDeniedException` on
`s3tables:CreateTableBucket`, `GetTableBucket`, etc.

Apply once per account:

```bash
cat > /tmp/s3tables-provisioning-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "S3TablesForDataZoneProvisioning",
    "Effect": "Allow",
    "Action": [
      "s3tables:CreateTableBucket",
      "s3tables:DeleteTableBucket",
      "s3tables:GetTableBucket",
      "s3tables:GetTableBucketMaintenanceConfiguration",
      "s3tables:PutTableBucketMaintenanceConfiguration",
      "s3tables:GetTableBucketPolicy",
      "s3tables:PutTableBucketPolicy",
      "s3tables:DeleteTableBucketPolicy",
      "s3tables:ListTableBuckets",
      "s3tables:CreateNamespace",
      "s3tables:GetNamespace",
      "s3tables:ListNamespaces",
      "s3tables:DeleteNamespace",
      "s3tables:CreateTable",
      "s3tables:GetTable",
      "s3tables:GetTableMaintenanceConfiguration",
      "s3tables:PutTableMaintenanceConfiguration",
      "s3tables:GetTableMetadataLocation",
      "s3tables:UpdateTableMetadataLocation",
      "s3tables:GetTablePolicy",
      "s3tables:PutTablePolicy",
      "s3tables:DeleteTablePolicy",
      "s3tables:ListTables",
      "s3tables:DeleteTable",
      "s3tables:RenameTable",
      "s3tables:PutTableData",
      "s3tables:GetTableData"
    ],
    "Resource": "*"
  }]
}
EOF

aws iam put-role-policy \
  --role-name AmazonSageMakerProvisioning-608050308596 \
  --policy-name S3TablesProvisioningSupplement \
  --policy-document file:///tmp/s3tables-provisioning-policy.json
```

> The role name is `AmazonSageMakerProvisioning-<account-id>`. The
> assumed-role ARN in IAM errors looks like
> `assumed-role/AmazonSageMakerProvisioning-<id>/AmazonDataZoneEnvironmentDeployer-<id>`
> — the part **between** `assumed-role/` and the next `/` is the role.
> The trailing segment is just the session name. Patch the first part.

> **Always use `Resource: "*"` on this policy** — do not scope to a
> specific bucket ARN. The provisioning role's job is to spin up *any*
> bucket SMUS requests at catalog-creation time. Scoping to a specific
> bucket means every new Lakehouse Catalog provisioning triggers a
> patch-the-policy treadmill.

## Phase 2 — Verify project profile has LakehouseCatalog on-demand

Domain Management → Project profiles → **All capabilities** → confirm
`LakehouseCatalog` is listed with **on-demand mode** enabled.

If on-demand isn't toggled, the wizard later fails with:
> On-demand mode must be enabled in your blueprint deployment settings to
> create new resources.

The `Admin-project-profile` is system-locked — cannot be edited. The
other three (SQL Analytics, Gen AI, All capabilities) are user-editable.

## Phase 3 — Create the user project

Domain Management → Create project:
- **Name**: e.g. `guardrail-agent`
- **Profile**: All capabilities
- **Owner**: your Identity Center user (`ms3dm-admin`)

After creation, Project Overview → copy the **Execution role ARN**.
Format: `arn:aws:iam::608050308596:role/service-role/AmazonSageMakerUserIAMExecutionRole_<hash>`.
Substitute into Phase 4 commands.

> Do all subsequent work **inside this user project**, not the
> Admin project. Admin's profile is locked; user-facing capabilities
> (data sources, table creation, Lakehouse Catalog provisioning) all
> require an editable profile.

## Phase 4 — Bring in an existing Glue catalog database

For `guardrailed_agent_demo` (or any other existing Glue database), use
the AWS-published migration script — it handles LF opt-in, S3 location
registration, and permission grants in one shot.

```bash
# One-time clone of the script
git clone https://github.com/aws/Unified-Studio-for-Amazon-Sagemaker.git
cd Unified-Studio-for-Amazon-Sagemaker/migration/bring-your-own-gdc-assets

# Attach the prerequisite IAM policy to your CLI identity (ms3dm-admin)
# if it doesn't already have admin-equivalent permissions. The README
# lists the exact actions; AdministratorAccess covers it.

# Verify you're acting as the right principal
aws sts get-caller-identity --region us-east-1

# Import the entire database into the user project
python3 bring_your_own_gdc_assets.py \
  --project-role-arn arn:aws:iam::608050308596:role/service-role/AmazonSageMakerUserIAMExecutionRole_<hash> \
  --database-name guardrailed_agent_demo \
  --region us-east-1
```

Manual alternative (if the script isn't an option): in the LF console,
grant the project execution role:
- `DESCRIBE` on database `guardrailed_agent_demo`
- `SELECT` + `DESCRIBE` on tables (all tables, or use the LF tag policy)

Either way, after Phase 4 the database is visible in the user project's
Data tab and queryable from Query Editor / JupyterLab.

## Phase 5 — Create S3 Tables resources (skip SMUS UI)

S3 Tables resources can be created entirely outside SMUS via CLI — much
faster than the SMUS provisioning workflow, and reproducible.

```bash
# Create the table bucket
aws s3tables create-table-bucket \
  --name ms3dm-medallion-prod \
  --region us-east-1

# Note the returned ARN, e.g.:
# arn:aws:s3tables:us-east-1:608050308596:bucket/ms3dm-medallion-prod

# Create a namespace (appears as a database in Glue)
aws s3tables create-namespace \
  --table-bucket-arn arn:aws:s3tables:us-east-1:608050308596:bucket/ms3dm-medallion-prod \
  --namespace bronze \
  --region us-east-1
```

Tables are created via Athena DDL — see Phase 7.

> **Why CLI over SMUS UI:** SMUS's Lakehouse Catalog provisioning goes
> through a CloudFormation-backed blueprint that's slower (1–3 minutes),
> harder to script, and requires Phase 1's IAM patch to even succeed.
> CLI is ~2 seconds and works with any IAM identity that has admin or
> equivalent. Use SMUS provisioning only if you specifically need the
> Lakehouse Catalog blueprint's project-asset wiring.

## Phase 5b — Grant LF permissions on the new catalog to the project role (CLI path only)

**Skip if you provisioned via SMUS UI.** SMUS automatically grants LF
permissions to the provisioning project's role (with a `projectId ==`
conditional grant) when you create a Lakehouse Catalog through the UI.
**The CLI path skips that step**, leaving the new catalog visible at
the Glue federation layer but not surfaced in the SMUS Data tab or the
Query Editor's catalog selector.

Validated 2026-05-15: without these LF grants on a CLI-created bucket,
Query Editor's database selector shows "no databases available"
despite the namespace existing at the s3tables level.

```bash
PROJECT_ROLE=arn:aws:iam::608050308596:role/datazone_usr_role_<project-id>_<env-id>
SUBCATALOG=608050308596:s3tablescatalog/<bucket-name>

# Catalog-level: SUPER_USER + DESCRIBE so SMUS surfaces it in the tree
aws lakeformation grant-permissions \
  --principal DataLakePrincipalIdentifier="$PROJECT_ROLE" \
  --resource "{\"Catalog\":{\"Id\":\"$SUBCATALOG\"}}" \
  --permissions SUPER_USER CREATE_DATABASE DESCRIBE \
  --region us-east-1

# Database-level on the namespace
aws lakeformation grant-permissions \
  --principal DataLakePrincipalIdentifier="$PROJECT_ROLE" \
  --resource "{\"Database\":{\"CatalogId\":\"$SUBCATALOG\",\"Name\":\"<namespace>\"}}" \
  --permissions ALL DESCRIBE CREATE_TABLE \
  --region us-east-1

# Table-wildcard: full perms on tables created later
aws lakeformation grant-permissions \
  --principal DataLakePrincipalIdentifier="$PROJECT_ROLE" \
  --resource "{\"Table\":{\"CatalogId\":\"$SUBCATALOG\",\"DatabaseName\":\"<namespace>\",\"TableWildcard\":{}}}" \
  --permissions ALL SELECT INSERT DESCRIBE ALTER DROP \
  --region us-east-1
```

After these grants land, refresh the SMUS Data explorer (circular icon
at the top of the tree). The catalog and database should appear in
the selector.

> Why this works around DataZone's design: SMUS's UI-driven
> provisioning embeds the project's session context (its projectId)
> in the LF grant condition. That's how the catalog "knows" which
> project owns it. CLI-created buckets have no such marking, so SMUS
> doesn't surface them in any project's view by default — they're
> visible only to roles with explicit LF perms. The grants above give
> the project's role those explicit perms.

## Phase 6 — Grant the project user role access to the S3 Tables bucket

**Not in AWS docs.** So the user project can browse and query the new
S3 Tables resources. AWS's published flow says "grant LF Super User to
the project role" is enough; in practice the project role also needs
the two IAM inline policies below to satisfy the underlying API
authorization layer.

The dynamic user role for the project follows the pattern
`datazone_usr_role_<project-id>_<env-id>`. Find it with:

```bash
aws iam list-roles --query 'Roles[?starts_with(RoleName,`datazone_usr_role_`)].RoleName' --output text
```

The role needs **two inline policies** — one for the s3tables service
API and one for Glue actions on the s3tables federated sub-catalogs.
Both are required; missing either leaves the catalog visible-but-not-
expandable in the SMUS Data tab.

### 6a — s3tables API access (runtime)

```bash
cat > /tmp/s3tables-user-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "S3TablesAccess",
    "Effect": "Allow",
    "Action": [
      "s3tables:GetTableBucket",
      "s3tables:GetTableBucketPolicy",
      "s3tables:ListTableBuckets",
      "s3tables:GetNamespace",
      "s3tables:ListNamespaces",
      "s3tables:CreateNamespace",
      "s3tables:CreateTable",
      "s3tables:GetTable",
      "s3tables:GetTableMetadataLocation",
      "s3tables:UpdateTableMetadataLocation",
      "s3tables:ListTables",
      "s3tables:DeleteTable",
      "s3tables:PutTableData",
      "s3tables:GetTableData"
    ],
    "Resource": "*"
  }]
}
EOF

aws iam put-role-policy \
  --role-name datazone_usr_role_<project-id>_<env-id> \
  --policy-name S3TablesAccess \
  --policy-document file:///tmp/s3tables-user-policy.json
```

### 6b — Glue actions on s3tables sub-catalogs (catalog enumeration)

This is the **critical and least-documented patch**. The AWS-managed
`SageMakerStudioProjectUserRolePolicy` grants `glue:GetDatabases` and
related actions only on the root `catalog/s3tablescatalog` resource,
**not on the per-bucket sub-catalogs** (`catalog/s3tablescatalog/<bucket>`).
Without this, you can see the catalog node in the SMUS Data tab but it
won't expand to show the namespace.

```bash
cat > /tmp/glue-s3tables-subcatalog-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "GlueOnS3TablesSubCatalogs",
    "Effect": "Allow",
    "Action": [
      "glue:GetCatalog",
      "glue:GetCatalogs",
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartitions",
      "glue:GetPartition",
      "glue:SearchTables",
      "glue:CreateDatabase",
      "glue:DeleteDatabase",
      "glue:CreateTable",
      "glue:DeleteTable",
      "glue:UpdateTable"
    ],
    "Resource": [
      "arn:aws:glue:us-east-1:608050308596:catalog/s3tablescatalog",
      "arn:aws:glue:us-east-1:608050308596:catalog/s3tablescatalog/*",
      "arn:aws:glue:us-east-1:608050308596:database/s3tablescatalog/*",
      "arn:aws:glue:us-east-1:608050308596:table/s3tablescatalog/*"
    ]
  }]
}
EOF

aws iam put-role-policy \
  --role-name datazone_usr_role_<project-id>_<env-id> \
  --policy-name GlueS3TablesSubCatalogAccess \
  --policy-document file:///tmp/glue-s3tables-subcatalog-policy.json
```

> The `datazone_usr_role_*` role is dynamically managed by DataZone. If
> the project's environment is recreated, both inline policies may be
> wiped. For long-lived setups, bake these perms into the project
> profile's environment role template via the DataZone Console or API
> instead of patching the live role. For short-lived demo/test work,
> the inline policies are fine.

## Phase 7 — Create tables and run queries (Athena via DataZone)

**In AWS docs.** Standard Athena-DDL-over-federated-catalog pattern.

From the AWS Athena console:

1. Top-right environment selector → switch from **Workgroup** to
   **DataZone environment**.
2. Pick the user project's environment
   (`AmazonSagemakerEnvironmentConfig-<env-id>`).
3. The "Query" button now deep-links into the user project's SMUS Query
   Editor. Click it.

The session in SMUS now carries the project context that satisfies the
LF conditional grants. Run CREATE TABLE:

```sql
CREATE TABLE "s3tablescatalog/ms3dm-medallion-prod"."bronze"."events" (
  event_id BIGINT,
  ts TIMESTAMP,
  payload STRING
)
TBLPROPERTIES ('table_type'='ICEBERG');
```

Subsequent queries (SELECT, INSERT, MERGE) work against the same table.

## Verification checklist

After running all phases, confirm:

- [ ] `aws s3tables list-table-buckets --region us-east-1` shows your
      bucket
- [ ] `aws s3tables list-namespaces --table-bucket-arn <arn> --region us-east-1`
      shows the namespace
- [ ] `aws s3tables list-tables --table-bucket-arn <arn> --namespace <ns> --region us-east-1`
      shows tables created via Athena DDL
- [ ] In the user project's SMUS UI → Data → Catalogs, you can see
      `s3tablescatalog/<bucket-name>` AND **the sub-catalog node
      expands to show the namespace database** (if it doesn't expand,
      Phase 6b's Glue policy is missing)
- [ ] `AwsDataCatalog/guardrailed_agent_demo` (from Phase 4) is also
      visible in the catalog tree
- [ ] A SELECT query from Query Editor against either catalog returns
      rows (or zero rows, but no permission error)
- [ ] `aws lakeformation list-permissions --resource '{"Catalog":{"Id":"<account>:s3tablescatalog/<bucket-name>"}}' --region us-east-1`
      shows the user project's conditional grant
- [ ] `aws iam list-role-policies --role-name datazone_usr_role_<project-id>_<env-id>`
      returns **both** `S3TablesAccess` and
      `GlueS3TablesSubCatalogAccess`

## Common pitfalls (and where they're handled above)

| Symptom | Cause | Where handled |
|---|---|---|
| Domain Management has only Projects/Users/Settings; no Blueprints | IAM-only auth mode | Phase 0 |
| "On-demand mode must be enabled... Admin-project-profile" | Trying to provision in Admin project (locked profile) | Phase 3 |
| `s3tables:CreateTableBucket` denied during provisioning | Default SageMakerProvisioning role lacks s3tables actions | Phase 1 |
| `s3tables:GetTableBucket` denied on a new bucket after Phase 1 patch | Phase 1 policy was scoped to a specific bucket instead of `*` | Phase 1 (always use `Resource: *`) |
| `s3tables:ListNamespaces` denied at query time | datazone_usr_role lacks s3tables actions | Phase 6a |
| **Catalog visible in SMUS Data tab but won't expand to show database/namespace** | **datazone_usr_role lacks `glue:GetDatabases` on s3tablescatalog sub-catalogs** | **Phase 6b** |
| Catalog selector in Query Editor errors: "specified bucket does not exist" | Federated catalog has an orphan sub-catalog referencing a deleted S3 Tables bucket | Cleanup section below |
| Glue database visible but queries return AccessDenied | Project role missing LF grants | Phase 4 |
| Catalog visible in tree but tables empty | Bucket has no namespaces, or namespace has no tables | Phase 5 / 7 |
| Query Editor catalog selector says "no databases available" for a CLI-created bucket | CLI path skips the LF grants that SMUS UI provisioning normally adds | Phase 5b |
| "Create table" wizard only offers AwsDataCatalog | SMUS table-creation UI is Glue-only; use Athena DDL | Phase 7 |
| Athena query "no permission" with conditional LF grant | Workgroup mode selected instead of DataZone environment | Phase 7 |
| Account has two SMUS domains and you didn't expect that | Easy to create a second domain accidentally; the "Create domain" and "Create project" flows look similar | See "Domain housekeeping" below |

## Orphan cleanup (after a failed catalog provisioning or deleted project)

When a Lakehouse Catalog provisioning fails partway through, or when
you delete a SMUS project whose Lakehouse Catalog you provisioned, the
underlying S3 Tables bucket persists and the federated Glue sub-catalog
keeps pointing at it. If the bucket is later deleted (or the LF
conditional grant references a now-deleted project ID), the
`s3tablescatalog` enumeration in the SMUS Data tab and Athena catalog
selector will start throwing `"specified bucket does not exist"` errors
that block selecting *any* sub-catalog.

Cleanup recipe (bucket → namespace → table delete in *reverse* order):

```bash
BUCKET=arn:aws:s3tables:us-east-1:608050308596:bucket/<orphan-bucket-name>

# Inspect contents first
aws s3tables list-namespaces --table-bucket-arn "$BUCKET" --region us-east-1
aws s3tables list-tables --table-bucket-arn "$BUCKET" --namespace <ns> --region us-east-1

# Delete in order
aws s3tables delete-table --table-bucket-arn "$BUCKET" --namespace <ns> --name <table> --region us-east-1
aws s3tables delete-namespace --table-bucket-arn "$BUCKET" --namespace <ns> --region us-east-1
aws s3tables delete-table-bucket --table-bucket-arn "$BUCKET" --region us-east-1
```

LF permissions on the corresponding federated sub-catalog
(`s3tablescatalog/<bucket-name>`) auto-cleanup once the bucket is gone.

## Domain housekeeping

A SMUS account can have multiple DataZone domains. List them with:

```bash
aws datazone list-domains --region us-east-1 --query 'items[].[id,name,status]' --output table
```

The AWS Console's SageMaker page may filter the visible domain list by
your console session's access pattern (Identity Center IDP domains
shown if the console can deep-link into the SMUS portal; "IAM-based"
domains shown for direct IAM access). It's possible to have domains
that aren't visible in the console but exist via API. The console
section header "Identity Center based domains" is more reliable than
the API field `singleSignOn.type` for telling which mode the console
treats a domain as.

## What to skip

- **Don't try Lakehouse Catalog provisioning in the Admin project.**
  Profile is locked. Will always fail.
- **Don't use the SMUS "+ Add → Create S3 Tables catalog" wizard for
  routine work.** It's CloudFormation-backed and slower than CLI. Use
  only if you specifically need the SMUS-side asset wiring.
- **Don't expect the SMUS "+ Add → Create table" wizard to work for S3
  Tables targets.** It's Glue-only by design (current AWS limitation).

## References

- **AWS canonical docs (what AWS publishes — incomplete relative to
  managed-policy reality, see top of this runbook):**
  - [Amazon S3 Tables integration](https://docs.aws.amazon.com/sagemaker-lakehouse-architecture/latest/userguide/lakehouse-s3-tables-integration.html)
  - [Working with Amazon S3 Tables in the lakehouse architecture of Amazon SageMaker](https://docs.aws.amazon.com/sagemaker-lakehouse-architecture/latest/userguide/s3-tables-integration.html)
- AWS Labs migration tool — `bring-your-own-gdc-assets`:
  https://github.com/aws/Unified-Studio-for-Amazon-Sagemaker/tree/main/migration/bring-your-own-gdc-assets
- This repo's existing operator runbook: `docs/operator-runbook.md`
- Lake Formation tag policy for the demo's Glue database:
  `terraform/modules/data-plane/main.tf:226` (dispatcher),
  `terraform/modules/data-plane/main.tf:252` (technician_lead)
- Memory note on SMUS auth modes:
  `~/.claude/projects/-Users-ryanfranklin-repos-guardrailed-agent/memory/reference_smus_identity_center_required.md`
