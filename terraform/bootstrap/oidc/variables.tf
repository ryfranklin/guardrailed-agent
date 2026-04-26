variable "region" {
  description = "AWS region for the bootstrap apply. IAM is global, so this is mostly for the provider's location."
  type        = string
  default     = "us-east-1"
}

variable "github_owner" {
  description = "GitHub owner (org or user) hosting the repo."
  type        = string
}

variable "github_repo" {
  description = "GitHub repo name. Defaults to guardrailed-agent."
  type        = string
  default     = "guardrailed-agent"
}

variable "role_name" {
  description = "Name of the CI role."
  type        = string
  default     = "guardrailed-agent-ci"
}

variable "allowed_refs" {
  description = "GitHub OIDC sub-claim patterns the CI role trusts. Default: any branch + any pull_request + tags."
  type        = list(string)
  default = [
    "ref:refs/heads/main",
    "pull_request",
    "ref:refs/tags/*",
  ]
}

variable "tfstate_bucket_arn" {
  description = "S3 bucket ARN holding terraform state. Empty string disables the scoped policy and falls back to wildcard (only acceptable for v1 dev)."
  type        = string
  default     = ""
}

variable "tfstate_lock_table_arn" {
  description = "DynamoDB table ARN for terraform state locking. Empty string falls back to wildcard."
  type        = string
  default     = ""
}
