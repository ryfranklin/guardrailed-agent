variable "env" {
  description = "Environment name (e.g., demo, client-acme). Used in role naming."
  type        = string
}

variable "role_name_prefix" {
  description = "Prefix for IAM role names. Default 'gagent-' produces gagent-analyst-<env>, etc."
  type        = string
  default     = "gagent-"
}

variable "trusted_assumer_arns" {
  description = "IAM principal ARNs allowed to assume the persona roles (operators, CI, the Lambda exec role)."
  type        = list(string)
}

variable "data_bucket_arns" {
  description = "S3 bucket ARNs the persona roles need read access to (raw data + Athena results)."
  type        = list(string)
}

variable "glue_database_name" {
  description = "Glue database the persona roles can DESCRIBE."
  type        = string
}

variable "athena_workgroup_name" {
  description = "Athena workgroup the persona roles can run queries in."
  type        = string
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}
