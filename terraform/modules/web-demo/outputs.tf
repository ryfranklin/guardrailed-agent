output "bucket_name" {
  description = "Name of the S3 origin bucket. The CI workflow consumes this via SSM (set in §5.7) to run `aws s3 sync web/dist/ s3://<bucket>/ --delete`."
  value       = aws_s3_bucket.this.bucket
}

output "bucket_arn" {
  description = "ARN of the S3 origin bucket."
  value       = aws_s3_bucket.this.arn
}

output "bucket_regional_domain_name" {
  description = "Regional S3 domain name; used internally as the CloudFront origin."
  value       = aws_s3_bucket.this.bucket_regional_domain_name
}

output "distribution_id" {
  description = "CloudFront distribution ID. The CI workflow consumes this via SSM (set in §5.7) to run `aws cloudfront create-invalidation --distribution-id <id> --paths '/*'`."
  value       = aws_cloudfront_distribution.this.id
}

output "distribution_arn" {
  description = "CloudFront distribution ARN."
  value       = aws_cloudfront_distribution.this.arn
}

output "distribution_domain_name" {
  description = "CloudFront distribution domain name (e.g., dxxxxxx.cloudfront.net). The operator points the CNAME for var.domain_name at this value at IONOS (§5.8)."
  value       = aws_cloudfront_distribution.this.domain_name
}

output "distribution_hosted_zone_id" {
  description = "CloudFront's hosted zone ID. Useful if/when the operator switches DNS to Route53 and creates an alias record."
  value       = aws_cloudfront_distribution.this.hosted_zone_id
}

output "acm_certificate_arn" {
  description = "ARN of the ACM cert backing the distribution."
  value       = aws_acm_certificate.this.arn
}

output "acm_certificate_validation_records" {
  description = "DNS records the operator must add at IONOS to validate the ACM cert. One record per domain (the primary plus any SANs). Empty after validation completes — the records can be removed at IONOS once the cert is issued."
  value = [
    for v in aws_acm_certificate.this.domain_validation_options : {
      domain = v.domain_name
      name   = v.resource_record_name
      type   = v.resource_record_type
      value  = v.resource_record_value
    }
  ]
}

output "spa_fallback_function_arn" {
  description = "ARN of the CloudFront viewer-request function rewriting client-side routes to /index.html."
  value       = aws_cloudfront_function.spa_fallback.arn
}
