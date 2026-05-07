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
  description = "Absolute path to the Lambda source directory. Defaults to ../../lambdas/governed_query relative to envs/<env>."
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
  description = "Athena results bucket ARN. Used as the storage target for the OpenAPI schema mirror."
  type        = string
}

variable "glue_database_name" {
  description = "Glue database name passed to the Lambda as an environment variable."
  type        = string
}

variable "persona_role_arns" {
  description = "ARNs of the persona roles the Lambda may assume (Dispatcher, TechnicianLead, Owner)."
  type        = list(string)
}

variable "invocation_log_group" {
  description = "CloudWatch log group the Lambda emits structured invocation telemetry to (gagent_client.emit_invocation_log)."
  type        = string
}

variable "invocation_log_group_arn" {
  description = "ARN of the invocation log group. Used to scope the log-write IAM grant."
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
