variable "name_prefix" {
  description = "Prefix for SNS topic and alarm names. Final names are <prefix>bedrock-cost-* (e.g., gagent-bedrock-cost-warn)."
  type        = string
  default     = "gagent-"
}

variable "env" {
  description = "Environment name (e.g., demo). Suffix on SNS topic and alarm names."
  type        = string
}

variable "notification_email" {
  description = "Operator email that receives SNS notifications when an alarm fires. AWS sends a confirmation email after the first apply; the subscription stays in PendingConfirmation state until the operator clicks the confirmation link, and alarms cannot notify until then."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must look like an email address."
  }
}

variable "warn_threshold_usd" {
  description = "USD threshold for the warn-tier Bedrock spend alarm. ADR-013 §5.2 default 50/day, calibrated to ~10x normal demo steady-state spend."
  type        = number
  default     = 50
}

variable "hard_stop_threshold_usd" {
  description = "USD threshold for the hard-stop-tier Bedrock spend alarm. ADR-013 §5.2 default 200/day; when this fires the runbook (docs/abuse-response.md) prescribes immediate containment."
  type        = number
  default     = 200
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}
