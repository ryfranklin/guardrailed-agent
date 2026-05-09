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

variable "invocation_log_group" {
  description = "CloudWatch log group for the gagent invocation telemetry stream. Surfaced by AgentCore Observability alongside the agent's auto-emitted X-Ray traces."
  type        = string
  default     = "/gagent/invocations"
}

variable "invocation_log_retention_days" {
  description = "Retention for the invocation log group. Default 30 days for the demo; client deployments may want longer."
  type        = number
  default     = 30
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

# ---- Phase 3.a — public web demo ----

variable "web_domain_name" {
  description = "Public domain the SPA is served from. Sets the CloudFront alternate domain, ACM cert CN, Cognito callback URLs, and CORS origin."
  type        = string
  default     = "demo.ms3dm.tech"
}

variable "gateway_persona_resolution_mode" {
  description = "Cognito persona resolution mode for the gateway Lambda. request-param (Shape A) for the public demo; claim-bound (Shape B) for client deployments."
  type        = string
  default     = "request-param"

  validation {
    condition     = contains(["request-param", "claim-bound"], var.gateway_persona_resolution_mode)
    error_message = "gateway_persona_resolution_mode must be 'request-param' or 'claim-bound'."
  }
}

variable "gateway_default_service_region" {
  description = "Optional fallback service_region for technician_lead in claim-bound mode. Unused in request-param (the demo's default)."
  type        = string
  default     = null
}

variable "gateway_rate_limit_per_5min" {
  description = "Per-IP WAF rate limit on the gateway HTTP API. ADR-010 §5 default 100."
  type        = number
  default     = 100
}

variable "google_client_id" {
  description = "Google OAuth client ID for Cognito federation. Pass via TF_VAR_google_client_id; never commit."
  type        = string
  sensitive   = true
}

variable "google_client_secret" {
  description = "Google OAuth client secret. Pass via TF_VAR_google_client_secret; never commit."
  type        = string
  sensitive   = true
}

variable "github_client_id" {
  description = "GitHub OAuth app client ID (wrapped via Cognito's generic OIDC provider). Pass via TF_VAR_github_client_id; never commit."
  type        = string
  sensitive   = true
}

variable "github_client_secret" {
  description = "GitHub OAuth app client secret. Pass via TF_VAR_github_client_secret; never commit."
  type        = string
  sensitive   = true
}

variable "slack_client_id" {
  description = "Slack 'Sign in with Slack' OIDC client ID. Pass via TF_VAR_slack_client_id; never commit."
  type        = string
  sensitive   = true
}

variable "slack_client_secret" {
  description = "Slack 'Sign in with Slack' OIDC client secret. Pass via TF_VAR_slack_client_secret; never commit."
  type        = string
  sensitive   = true
}
