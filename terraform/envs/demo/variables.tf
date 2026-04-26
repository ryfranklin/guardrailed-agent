variable "region" {
  description = "AWS region. us-east-1 is the recommended default for Bedrock model breadth."
  type        = string
  default     = "us-east-1"
}

variable "foundation_model_id" {
  description = "Bedrock model or inference profile ID. Default: us.anthropic.claude-sonnet-4-6 (US cross-region profile, required for Sonnet 4.6 invocation)."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-6"
}

variable "lf_admin_principal_arns" {
  description = "Additional Lake Formation admin principal ARNs beyond the deploying caller identity. Operators who need to administer LF outside of CI."
  type        = list(string)
  default     = []
}

variable "trusted_assumer_arns" {
  description = "Additional IAM principals allowed to assume the persona roles beyond the deploying caller identity and the Lambda exec role. Typically: CI roles, on-call operator roles."
  type        = list(string)
  default     = []
}

variable "langfuse_host" {
  description = "Langfuse host URL."
  type        = string
  default     = "https://cloud.langfuse.com"
}

variable "langfuse_public_key" {
  description = "Langfuse public key."
  type        = string
  sensitive   = true
}

variable "langfuse_secret_key" {
  description = "Langfuse secret key."
  type        = string
  sensitive   = true
}

variable "smus_reader_role_arns" {
  description = <<-EOT
    IAM role ARNs of SageMaker Unified Studio project execution roles that should see the governed database in SMUS.
    Each gets the same Lake Formation grants as the Admin persona (SELECT on all tables, DESCRIBE on the database) so
    the data team can browse and query the catalog from a Studio notebook. For per-client deployments, leave empty
    if SMUS is not used in the target account.
  EOT
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags merged into the per-env default tags."
  type        = map(string)
  default     = {}
}
