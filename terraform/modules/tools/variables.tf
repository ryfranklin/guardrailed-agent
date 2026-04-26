variable "env" {
  description = "Environment name (e.g., demo). Used in Lambda naming."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for Lambda function name."
  type        = string
  default     = "gagent-"
}

variable "lambda_source_dir" {
  description = "Absolute path to the Lambda source directory. Defaults to ../../lambdas/query_ambassadors relative to envs/<env>."
  type        = string
}

variable "lambda_runtime" {
  description = "Python runtime for the Lambda."
  type        = string
  default     = "python3.12"
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds. Athena queries are async; the Lambda polls."
  type        = number
  default     = 60
}

variable "lambda_memory" {
  description = "Lambda memory size in MB."
  type        = number
  default     = 512
}

variable "athena_workgroup_name" {
  description = "Athena workgroup the Lambda runs queries in."
  type        = string
}

variable "athena_results_bucket_arn" {
  description = "Athena results bucket ARN — needed for Lambda to read query results back."
  type        = string
}

variable "data_bucket_arn" {
  description = "Raw data bucket ARN — needed for IAM least-privilege scoping."
  type        = string
}

variable "glue_database_name" {
  description = "Glue database name passed to the Lambda as an environment variable."
  type        = string
}

variable "persona_role_arns" {
  description = "ARNs of the persona roles the Lambda may assume (Analyst, RegionalManager, Admin)."
  type        = list(string)
}

variable "langfuse_secret_arn" {
  description = "Secrets Manager ARN holding the Langfuse public/secret keys. Lambda reads this at runtime."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}
