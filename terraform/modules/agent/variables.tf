variable "env" {
  description = "Environment name (e.g., demo). Used in agent naming."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for the agent name. Final name is <prefix><env>."
  type        = string
  default     = "gagent-"
}

variable "foundation_model_id" {
  description = <<-EOT
    Bedrock model identifier passed to the agent. Accepts either a foundation model ID
    (`anthropic.claude-sonnet-4-6`) or a cross-region inference profile ID
    (`us.anthropic.claude-sonnet-4-6`). The module detects inference profiles by their
    region prefix and grants InvokeModel on both the profile and the underlying foundation
    model. Default is the US inference profile for Sonnet 4.6, which is required for
    Anthropic models that only support INFERENCE_PROFILE invocation.
  EOT
  type        = string
  default     = "us.anthropic.claude-sonnet-4-6"
}

variable "agent_instructions" {
  description = "System prompt for the agent. Refined through eval over time."
  type        = string
  default     = <<-EOT
    You are an analyst assistant for a direct-sales ambassador organization. You answer questions about ambassador performance, team health, and recent orders by querying the underlying governed dataset through your tools.

    Always honor the principle that the data system enforces what each user is permitted to see — never speculate about data your tool calls did not return. If a tool call returns redacted or masked values (literal "REDACTED" strings or null PII fields), treat them as redacted; do not infer, guess, or fill in.

    If a question is outside the ambassador domain (legal, medical, off-topic), politely decline.

    When you call a tool, include a clear question_intent so the trace is readable to a security reviewer.
  EOT
}

variable "guardrail_id" {
  description = "ID of the Bedrock Guardrail to attach. From the guardrails module."
  type        = string
}

variable "guardrail_version" {
  description = "Pinned Guardrail version."
  type        = string
}

variable "action_group_lambda_arn" {
  description = "Lambda ARN backing the query_ambassadors action group."
  type        = string
}

variable "action_group_openapi_schema" {
  description = "Inline OpenAPI 3 schema for the query_ambassadors action group."
  type        = string
}

variable "agent_alias_name" {
  description = "Name of the published agent alias."
  type        = string
  default     = "live"
}

variable "idle_session_ttl" {
  description = "Idle session TTL in seconds."
  type        = number
  default     = 1800
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}
