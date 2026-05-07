variable "env" {
  description = "Environment name (e.g., demo, client-acme). Used in resource naming."
  type        = string
}

variable "glue_database_name" {
  description = "Glue catalog database name. Iceberg tables for the governed entities land here."
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

variable "dispatcher_role_arn" {
  description = "IAM role ARN for the Dispatcher persona. Receives SELECT on pii=false AND sensitivity=other (ADR-008)."
  type        = string
}

variable "technician_lead_role_arn" {
  description = "IAM role ARN for the TechnicianLead persona. Receives SELECT on full PII row-filtered by service_region session tag, but no sensitivity=high columns (ADR-008)."
  type        = string
}

variable "owner_role_arn" {
  description = "IAM role ARN for the Owner persona. Receives unrestricted SELECT including sensitivity=high columns (ADR-008)."
  type        = string
}

variable "tags" {
  description = "Common resource tags applied to every taggable resource in the module."
  type        = map(string)
  default     = {}
}
