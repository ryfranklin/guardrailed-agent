# web-demo

S3 origin bucket + CloudFront distribution + ACM cert for the Phase 3.a
public web demo at `demo.ms3dm.tech`. Provisions infrastructure only —
the bundle (`web/dist/`) is deployed by the CI workflow, not by this
module.

Source-of-truth specs:
[phase-3a-brief §12](../../../docs/phase-3a-brief.md#12-web-demo-terraform-spec-terraformmoduleswebdemo),
[ADR-012 §6](../../../../consulting/guardrailed-agent/decisions/012-web-demo.md).

## Resource layout

- `main.tf` — S3 bucket (private, BucketOwnerEnforced, versioning, SSE-S3,
  public access blocked); CloudFront OAC; ACM cert + DNS validation;
  S3 bucket policy granting the OAC `s3:GetObject`.
- `cloudfront.tf` — viewer-request CloudFront function (SPA fallback);
  CloudFront distribution with the managed `CachingOptimized` cache
  policy, `redirect-to-https`, custom 403/404 error responses to
  `/index.html`.
- `variables.tf` / `outputs.tf` / `README.md` — interface.

## Region requirement

CloudFront only accepts ACM certs from `us-east-1`. The module's
`aws_acm_certificate` precondition errors out if the calling provider
is in any other region. The Phase 3.a demo env layer already targets
`us-east-1`. If you ever move the env elsewhere, add an `aws` provider
alias pointing at `us-east-1` and pass it to this module via
`providers = { aws = aws.us_east_1 }` from the env layer.

## Inputs

| Variable | Required | Default | Notes |
|---|---|---|---|
| `env` | yes | — | Environment name. Used in resource naming. |
| `domain_name` | yes | — | The public domain (e.g., `demo.ms3dm.tech`). Becomes the CloudFront alternate domain name and the ACM cert CN. |
| `name_prefix` | no | `gagent-` | Prefix for bucket name and OAC name. |
| `additional_domain_names` | no | `[]` | Additional aliases on the distribution + SANs on the ACM cert. |
| `price_class` | no | `PriceClass_100` | `PriceClass_100` = NA + EU edges (cheapest); `PriceClass_All` = global. |
| `minimum_protocol_version` | no | `TLSv1.2_2021` | Viewer TLS minimum. |
| `default_ttl_seconds` / `max_ttl_seconds` | no | 24h / 7d | Reserved for future custom cache policy; the default behavior currently uses the AWS-managed `CachingOptimized` policy. |
| `acm_validation_timeout` | no | `30m` | Window the operator has to add validation records at IONOS before `terraform apply` times out. |
| `tags` | no | `{}` | Common tags. |

## Outputs

| Output | Notes |
|---|---|
| `bucket_name` / `bucket_arn` / `bucket_regional_domain_name` | S3 origin identifiers. CI consumes `bucket_name` via SSM. |
| `distribution_id` / `distribution_arn` / `distribution_domain_name` | CloudFront identifiers. CI consumes `distribution_id` via SSM. `distribution_domain_name` (e.g., `dxxxxxx.cloudfront.net`) is the CNAME target at IONOS. |
| `distribution_hosted_zone_id` | Static CloudFront zone (`Z2FDTNDATAQYW2`); reserved for a future Route53 migration. |
| `acm_certificate_arn` | Cert ARN. |
| `acm_certificate_validation_records` | DNS records the operator adds at IONOS to validate the ACM cert (see below). |
| `spa_fallback_function_arn` | The viewer-request CloudFront function. |

## Bundle deploy boundary — TF provisions infra; CI deploys content

The Terraform module **does not** sync the `web/dist/` bundle to S3 and
**does not** invalidate CloudFront. This is intentional: keeping
content deploys out of `terraform apply` means the SPA can be
re-deployed (typo fix, copy tweak) without a Terraform run, and routine
deploys cannot accidentally touch infra.

The contract for §5.7's `web.yml` workflow:

```bash
BUCKET=$(aws ssm get-parameter \
  --name /gagent/${ENV}/web_bucket_name \
  --query Parameter.Value --output text)
DIST=$(aws ssm get-parameter \
  --name /gagent/${ENV}/web_distribution_id \
  --query Parameter.Value --output text)

aws s3 sync web/dist/ s3://${BUCKET}/ --delete
aws cloudfront create-invalidation \
  --distribution-id ${DIST} --paths "/*"
```

The env layer (§5.7) creates two `aws_ssm_parameter` resources mapping
those names to `module.web_demo.bucket_name` and
`module.web_demo.distribution_id`. The CI role needs
`ssm:GetParameter` on both names plus `s3:PutObject`/`s3:DeleteObject`
on the bucket and `cloudfront:CreateInvalidation` on the distribution.

## ACM validation flow (operator action — IONOS)

1. First `terraform apply` reaches `aws_acm_certificate_validation` and
   blocks polling ACM for up to `acm_validation_timeout` (default 30m).
2. In a second terminal, run `terraform output -raw acm_certificate_validation_records`
   in the env directory; it prints one record per domain (primary +
   SANs) with `name`, `type` (`CNAME`), and `value`.
3. At the IONOS DNS console, add each as a CNAME record. Strip the
   trailing dot from `name` if IONOS rejects it. The records have the
   form `_<random>.demo.ms3dm.tech CNAME _<random>.acm-validations.aws`.
4. ACM polls the records and finishes validation within a few minutes;
   the apply continues to provision the CloudFront distribution against
   the now-issued cert.
5. After the cert is `ISSUED`, the validation records can be deleted at
   IONOS — they are no longer needed (renewal goes through ACM-managed
   renewal as long as the records exist; for a v1 manual flow, plan to
   re-add them around expiry).

The DNS records consumed by visitors (the `demo.ms3dm.tech` CNAME
pointing to `distribution_domain_name`) are documented separately in
§5.8 (`docs/domains-and-dns.md`).

## SPA fallback

Two layers, intentionally redundant:

1. **CloudFront function (viewer-request)** rewrites any URI that has
   no extension and does not start with `/assets/` to `/index.html`.
   Cheap, runs at the edge before cache lookup.
2. **CloudFront custom error responses** rewrite 403 + 404 from S3 to
   `/index.html` with HTTP 200, cached for 10s.

Either alone would handle the SPA's client-side routes; running both
makes the behavior robust against edge cases (deep links to nonexistent
asset paths, etc.).

## Notes for §5.7 (env layer wiring)

The env layer adds:

```hcl
module "web_demo" {
  source      = "../../modules/web-demo"
  env         = local.env
  domain_name = "demo.ms3dm.tech"
  tags        = local.common_tags
}

resource "aws_ssm_parameter" "web_bucket_name" {
  name  = "/gagent/${local.env}/web_bucket_name"
  type  = "String"
  value = module.web_demo.bucket_name
}

resource "aws_ssm_parameter" "web_distribution_id" {
  name  = "/gagent/${local.env}/web_distribution_id"
  type  = "String"
  value = module.web_demo.distribution_id
}
```

Output `module.web_demo.acm_certificate_validation_records` so the
operator can read it via `terraform output` during the validation
window.
