variable "env" {
  description = "Environment name (e.g., demo). Used in log-group naming."
  type        = string
}

variable "log_group_name" {
  description = "CloudWatch log group for the gagent invocation telemetry stream. Surfaced by AgentCore Observability alongside the agent's auto-emitted X-Ray traces."
  type        = string
  default     = "/gagent/invocations"
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days. Defaults to 30 — a balance between audit window and storage cost."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}
