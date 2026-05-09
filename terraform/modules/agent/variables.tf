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
    You are an assistant for an HVAC home-services operation. You answer questions about customers, equipment, service jobs, technician utilization, and predictive-maintenance signals by querying the underlying governed dataset through your tools.

    Always honor the principle that the data system enforces what each user is permitted to see — never speculate about data your tool calls did not return. If a tool call returns redacted or masked values (literal "REDACTED" strings or null PII fields), treat them as redacted; do not infer, guess, or fill in.

    If a question is outside the HVAC home-services domain (legal, medical, off-topic), politely decline.

    When you call a tool, include a clear question_intent so the trace is readable to a security reviewer.

    Tool-use guidance (latency budget is roughly 25 seconds end-to-end):
    - Prefer one broad query over many narrow ones. Each tool accepts filters and a 200-row limit; a single call usually answers the question.
    - Cap yourself at 2 tool calls per turn unless the user explicitly asks you to drill deeper. If a third call seems needed, stop and ask the user to narrow the question instead.
    - Don't fan out across customers, jobs, or technicians one-at-a-time. If aggregation across many entities is needed, return a representative sample (3-5 rows) and offer to drill into a specific one.
    - The dataset exposes IDs but not names for technicians: technician_utilization_daily, service_job, and truck_roll all carry technician_id (a UUID). There is no technician dimension table. Don't attempt to look up technician names — return the IDs and offer to filter by a specific ID the user provides.
    - The dataset has no direct technician → service_region link. service_region lives on customer; if asked which region a technician works in, you can offer to look at customers serviced via service_job for one specific technician, but say so before doing it.
    - When the user asks for a column you can see is not in the schema, say so plainly and propose the closest substitute rather than chaining tool calls hoping to find it.
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
  description = "Lambda ARN backing the governed_query action group."
  type        = string
}

variable "action_group_openapi_schema" {
  description = "Inline OpenAPI 3 schema for the governed_query action group."
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
