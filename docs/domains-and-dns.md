# Domains and DNS — Phase 3.a

The apex `ms3dm.tech` is registered with IONOS and DNS is hosted at IONOS.
Phase 3.a does **not** migrate DNS to Route53 — two records added at IONOS
are enough to publish `demo.ms3dm.tech` over CloudFront with a trusted
ACM cert.

> Source-of-truth: [phase-3a-brief.md §13](./phase-3a-brief.md#13-dns-plan-docsdomains-and-dnsmd),
> [ADR-012 §6](../../consulting/guardrailed-agent/decisions/012-web-demo.md).

## Records the operator adds at IONOS

Two CNAME records. The first is permanent (visitors hit it on every
page load). The second is temporary — once the ACM cert is issued, the
record can be deleted.

| # | Name | Type | Target | Lifetime |
|---|---|---|---|---|
| 1 | `demo.ms3dm.tech` | CNAME | `<distribution_domain_name>` (e.g. `dxxxxxxxxx.cloudfront.net`) | Permanent |
| 2 | `<acm-emitted-name>.demo.ms3dm.tech` | CNAME | `<acm-emitted-target>` (e.g. `_xxxxxxx.acm-validations.aws.`) | Temporary; remove after cert is `ISSUED` |

Both records' targets come from `terraform output` after the first
`terraform apply` reaches `aws_acm_certificate`. The cert validation
record is **not** at the literal name `_acme-challenge.demo.ms3dm.tech` —
ACM emits a randomized name like `_a4b17e2c5d6f.demo.ms3dm.tech`, with
the corresponding target being `_xxxxxxx.acm-validations.aws.` Always
copy the live values out of `terraform output`; never hand-edit them.

## End-to-end flow

```
operator (laptop)              terraform                   IONOS                     ACM                CloudFront
       │                           │                          │                       │                      │
       ├── terraform apply ─────► (creates resources)         │                       │                      │
       │                           ├── ACM cert request ─────────────────────────────► (PENDING_VALIDATION)  │
       │                           │                          │                       │                      │
       │ ◄─ blocked at             │                          │                       │                      │
       │    aws_acm_certificate_   │                          │                       │                      │
       │    validation (polling)   │                          │                       │                      │
       │                           │                          │                       │                      │
       ├── terraform output -json acm_certificate_validation_records                  │                      │
       ◄────────────────────────── (records emitted)          │                       │                      │
       │                           │                          │                       │                      │
       ├── log in to IONOS ─────────────────────────────────► (DNS console)           │                      │
       ├── add temp CNAME #2 ─────────────────────────────────►                       │                      │
       │                           │                          │ <───── poll ──────────┤                      │
       │                           │                          │ (ACM finds record)    │                      │
       │                           │                          │                       │                      │
       │                           ◄─────────────── unblocks (ISSUED) ────────────────┤                      │
       │                           ├── creates distribution ────────────────────────────────────────────────►│
       │                           │                          │                       │                      │
       ◄────────────── apply complete ─────────────            │                       │                      │
       │                           │                          │                       │                      │
       ├── terraform output -raw web_distribution_domain_name │                       │                      │
       ◄────── dxxxxxxxxx.cloudfront.net                      │                       │                      │
       │                           │                          │                       │                      │
       ├── add permanent CNAME #1 at IONOS ───────────────────►                       │                      │
       │                           │                          │                       │                      │
       ├── (optional) delete temp CNAME #2 at IONOS ──────────►                       │                      │
       │                           │                          │                       │                      │
       └── visit https://demo.ms3dm.tech ─────────────────────────────────────────────────────────────────────► SPA loaded
```

## Step-by-step

### 1. First `terraform apply` — get the validation records

```bash
cd terraform/envs/demo
terraform apply
# (apply blocks at aws_acm_certificate_validation; default 30m polling window)
```

In a second terminal:

```bash
cd terraform/envs/demo
terraform output -json acm_certificate_validation_records | jq .
```

You'll see something like:

```json
[
  {
    "domain": "demo.ms3dm.tech",
    "name":   "_a4b17e2c5d6f.demo.ms3dm.tech.",
    "type":   "CNAME",
    "value":  "_8c4d2a1b9e3f.acm-validations.aws."
  }
]
```

### 2. Add the validation CNAME at IONOS

Log in to IONOS, navigate to **DNS settings for `ms3dm.tech`**, add a
new record:

- **Type:** `CNAME`
- **Hostname / Name:** the `name` value above, **with `.demo.ms3dm.tech.`
  stripped to just the prefix** — IONOS expects the relative-to-zone
  form. So `_a4b17e2c5d6f.demo.ms3dm.tech.` becomes
  `_a4b17e2c5d6f.demo`.
- **Points to:** the full `value` above (`_8c4d2a1b9e3f.acm-validations.aws.`).
  Keep the trailing dot if IONOS allows it; IONOS usually appends `.` automatically.
- **TTL:** any value (1 hour is fine).

Save. Within a few minutes ACM detects the record and the apply unblocks.

### 3. Add the permanent CNAME for the public domain

After apply finishes, get the CloudFront target:

```bash
terraform -chdir=terraform/envs/demo output -raw web_distribution_domain_name
# dxxxxxxxxx.cloudfront.net
```

Or via SSM:

```bash
aws ssm get-parameter \
  --name /gagent/demo/web_distribution_id \
  --query Parameter.Value --output text
```

(The actual CloudFront `<id>.cloudfront.net` is in the `web_distribution_domain_name`
output, not in SSM — only the distribution ID is mirrored to SSM. Use
`terraform output` for the domain.)

Back at IONOS:

- **Type:** `CNAME`
- **Hostname / Name:** `demo`
- **Points to:** `dxxxxxxxxx.cloudfront.net.`
- **TTL:** 1 hour for the launch (drop to 5 minutes during testing if
  you expect to swap distributions; raise to 24 hours once stable).

Save. DNS propagation typically takes < 15 minutes given a 1h TTL.

### 4. Optional — remove the temporary validation CNAME

Once the cert is in `ISSUED` state (visible in the AWS console under
ACM, or via `aws acm describe-certificate --certificate-arn ...`), the
record is no longer required for the cert to function. ACM-managed
renewal needs it back when expiry approaches; for a v1 manual flow,
plan to re-add it ~60 days before the cert's renewal date.

The record is harmless if left in place. The simplest plan: leave it.

## Why CNAME at the apex of `demo.`

CloudFront does not give a stable IP — it gives a hostname. For a
non-apex subdomain like `demo.ms3dm.tech`, a CNAME pointing at the
distribution's domain name works fine. The apex `ms3dm.tech` itself
cannot CNAME (RFC 1912 forbids CNAME at zone apex); if a future migration
moves the apex onto AWS, switching DNS to Route53 enables the
ALIAS-record workaround. Out of scope for Phase 3.a.

## Troubleshooting

- **ACM never validates** — verify the IONOS record's hostname does
  not include `ms3dm.tech.` twice (some panels concat the zone for you;
  others want the FQDN). Use `dig +short CNAME _a4b17e2c5d6f.demo.ms3dm.tech`
  to confirm what's actually published.
- **`terraform apply` times out at validation** — bump the timeout via
  the `acm_validation_timeout` variable in the env layer (default 30m).
  ACM polling is unmetered; longer windows are free.
- **`https://demo.ms3dm.tech` returns AccessDenied** — the bundle has
  not been deployed yet. Run the `web` GitHub Actions workflow on a
  push to `main`, or sync manually: `aws s3 sync web/dist/ s3://$(aws ssm
  get-parameter --name /gagent/demo/web_bucket_name --query Parameter.Value
  --output text)/ --delete`.
- **`https://demo.ms3dm.tech` returns NoSuchKey** — same as above; the
  CloudFront error response is configured to map 403/404 to
  `/index.html`, but the SPA bundle must exist in S3 first.

## Out of scope (Phase 3.5)

- Migrating DNS to Route53.
- Custom Cognito Hosted UI domain at `auth.ms3dm.tech`.
- Custom API Gateway domain at `api.demo.ms3dm.tech`.
- Apex `ms3dm.tech` ALIAS record onto CloudFront.

Each unblocks specific UX wins; none are required for Phase 3.a's
acceptance criteria.
