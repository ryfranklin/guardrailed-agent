variable "env" {
  description = "Environment name (e.g., demo). Used in user pool naming."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for the user pool name. Final name is <prefix><env> (e.g., gagent-demo)."
  type        = string
  default     = "gagent-"
}

variable "hosted_ui_domain_prefix" {
  description = <<-EOT
    Prefix for the Cognito-managed Hosted UI domain. Resolves to
    <prefix>.auth.<region>.amazoncognito.com. Must be globally unique within the
    Cognito-managed domain space. Defaults to <name_prefix><env> (e.g., gagent-demo).
    Custom domains (auth.example.com) are deferred to Phase 3.5.
  EOT
  type        = string
  default     = null
}

variable "callback_urls" {
  description = "Allowed OAuth callback URLs for the user pool client. Defaults cover the public demo and the Vite dev server."
  type        = list(string)
  default = [
    "https://demo.ms3dm.tech/auth/callback",
    "http://localhost:5173/auth/callback",
  ]
}

variable "logout_urls" {
  description = "Allowed logout URLs for the user pool client."
  type        = list(string)
  default = [
    "https://demo.ms3dm.tech/",
    "http://localhost:5173/",
  ]
}

variable "id_token_validity_minutes" {
  description = "ID token validity in minutes. ADR-007 set 60; ADR-013 §5.3 lowers to 30 to halve the blast radius of a leaked token."
  type        = number
  default     = 30
}

variable "access_token_validity_minutes" {
  description = "Access token validity in minutes. ADR-007 set 60; ADR-013 §5.3 lowers to 30 to halve the blast radius of a leaked token."
  type        = number
  default     = 30
}

variable "refresh_token_validity_days" {
  description = "Refresh token validity in days. Stays 30 per ADR-007; ADR-013 §5.3 explicitly preserves the refresh window for UX."
  type        = number
  default     = 30
}

variable "google_client_id" {
  description = "Google OAuth client ID for the federated IdP. Pass via TF_VAR_google_client_id; never commit."
  type        = string
  sensitive   = true
}

variable "google_client_secret" {
  description = "Google OAuth client secret. Pass via TF_VAR_google_client_secret; never commit."
  type        = string
  sensitive   = true
}

variable "github_client_id" {
  description = "GitHub OAuth app client ID. Wrapped via Cognito's generic OIDC provider (GitHub does not expose JWKS). Pass via TF_VAR_github_client_id."
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

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}
