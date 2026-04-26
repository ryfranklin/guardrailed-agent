output "data_bucket_name" {
  description = "Raw data bucket where Iceberg table data and metadata land."
  value       = aws_s3_bucket.data.bucket
}

output "data_bucket_arn" {
  description = "ARN of the raw data bucket."
  value       = aws_s3_bucket.data.arn
}

output "athena_results_bucket_name" {
  description = "Bucket Athena writes query results to."
  value       = aws_s3_bucket.athena_results.bucket
}

output "athena_results_bucket_arn" {
  description = "ARN of the Athena results bucket."
  value       = aws_s3_bucket.athena_results.arn
}

output "glue_database_name" {
  description = "Glue catalog database holding the four governed tables."
  value       = aws_glue_catalog_database.this.name
}

output "athena_workgroup_name" {
  description = "Athena workgroup used by the Lambda action group and the synthesizer."
  value       = aws_athena_workgroup.this.name
}

output "lf_tag_pii_key" {
  description = "Lake Formation tag key used to classify PII columns."
  value       = aws_lakeformation_lf_tag.pii.key
}

output "lf_tag_pii_values" {
  description = "Allowed values for the pii LF-Tag."
  value       = aws_lakeformation_lf_tag.pii.values
}
