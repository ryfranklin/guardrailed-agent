variable "env" {
  description = "Environment name (e.g., demo, client-acme). Used in resource naming."
  type        = string
}

variable "glue_database_name" {
  description = "Glue catalog database name. Iceberg tables for the four entities land here."
  type        = string
}

variable "s3_bucket_prefix" {
  description = "Prefix for S3 bucket names. Final bucket names are <prefix><env>-<purpose>-<account>."
  type        = string
  default     = "gagent-"
}

variable "lf_admin_principal_arns" {
  description = "IAM principal ARNs that act as Lake Formation administrators for this data plane."
  type        = list(string)
}

variable "analyst_role_arn" {
  description = "IAM role ARN for the Analyst persona. Receives SELECT on non-PII columns via LF-Tag expressions."
  type        = string
}

variable "regional_manager_role_arn" {
  description = "IAM role ARN for the RegionalManager persona. Receives SELECT on full PII, row-filtered by session tag region."
  type        = string
}

variable "admin_role_arn" {
  description = "IAM role ARN for the Admin persona. Receives unrestricted SELECT."
  type        = string
}

variable "tags" {
  description = "Common resource tags applied to every taggable resource in the module."
  type        = map(string)
  default     = {}
}
