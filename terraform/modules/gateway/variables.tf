variable "env" {
  description = "Environment name (e.g., demo). Used in resource naming."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for resource names. Defaults to gagent-."
  type        = string
  default     = "gagent-"
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}

# ---- Lambda packaging ----

variable "lambda_source_dir" {
  description = "Absolute path to the gateway Lambda source directory (lambdas/gateway)."
  type        = string
}

variable "gagent_client_source_dir" {
  description = "Absolute path to the gagent_client Python package. Bundled into the Lambda zip alongside the gateway handler so `import gagent_client` resolves at runtime."
  type        = string
}

variable "lambda_runtime" {
  description = "Python runtime for the gateway Lambda."
  type        = string
  default     = "python3.12"
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds. Bedrock InvokeAgent buffers can take 30-45s under load; default 60."
  type        = number
  default     = 60
}

variable "lambda_memory" {
  description = "Lambda memory in MB. Phase 3.a brief §10 sets 1024."
  type        = number
  default     = 1024
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the Lambda's own log group and the API GW access log group."
  type        = number
  default     = 30
}

# ---- Cognito (from auth/ module) ----

variable "cognito_user_pool_arn" {
  description = "Cognito user pool ARN (auth.user_pool_arn). Reserved for future trust policies; not directly consumed by the JWT authorizer."
  type        = string
}

variable "cognito_user_pool_endpoint" {
  description = "Cognito user pool endpoint without scheme, e.g. cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXX (auth.user_pool_endpoint). The module prepends https:// to form the JWT authorizer issuer."
  type        = string
}

variable "cognito_user_pool_client_id" {
  description = "Cognito user pool client ID (auth.user_pool_client_id). Becomes the JWT authorizer audience."
  type        = string
}

# ---- Bedrock Agent (from agent/ module) ----

variable "agent_id" {
  description = "Bedrock Agent ID (agent.agent_id). Injected into the Lambda env."
  type        = string
}

variable "agent_alias_id" {
  description = "Bedrock Agent alias ID (agent.agent_alias_id). Injected into the Lambda env."
  type        = string
}

variable "agent_alias_arn" {
  description = "Bedrock Agent alias ARN. Scoped target for the Lambda's bedrock:InvokeAgent grant."
  type        = string
}

# ---- governed_query Lambda (for the data-preview pass-through) ----

variable "governed_query_lambda_arn" {
  description = "ARN of the existing governed_query Lambda (action group implementation). Scoped target for the gateway Lambda's lambda:InvokeFunction grant on the /preview path."
  type        = string
}

variable "governed_query_lambda_name" {
  description = "Function name of the governed_query Lambda. Injected as GAGENT_GOVERNED_QUERY_LAMBDA_NAME on the gateway Lambda."
  type        = string
}

# ---- Persona roles (from identity/ module) ----

variable "persona_role_arns" {
  description = "Map of persona role name -> IAM role ARN. Keys must be exactly {dispatcher, technician_lead, owner}; the Lambda assumes these with session tags."
  type        = map(string)

  validation {
    condition = alltrue([
      for k in keys(var.persona_role_arns) :
      contains(["dispatcher", "technician_lead", "owner"], k)
    ]) && length(var.persona_role_arns) == 3
    error_message = "persona_role_arns must contain exactly the keys dispatcher, technician_lead, owner."
  }
}

# ---- Observability ----

variable "invocation_log_group" {
  description = "CloudWatch log group for /gagent/invocations (observability.invocation_log_group). Injected into the Lambda env so gagent_client.emit_invocation_log writes there."
  type        = string
}

variable "invocation_log_group_arn" {
  description = "ARN of the invocation log group. Used to scope the Lambda's logs:PutLogEvents grant."
  type        = string
}

# ---- Behavior ----

variable "persona_resolution_mode" {
  description = "Cognito persona resolution mode. request-param (Shape A) for the public demo; claim-bound (Shape B) for client deployments. Sets GAGENT_GATEWAY_PERSONA_RESOLUTION on the Lambda."
  type        = string
  default     = "request-param"

  validation {
    condition     = contains(["request-param", "claim-bound"], var.persona_resolution_mode)
    error_message = "persona_resolution_mode must be 'request-param' or 'claim-bound'."
  }
}

variable "default_service_region" {
  description = "Optional fallback service_region for technician_lead when claim-bound mode and the request body omits it. Sets GAGENT_DEFAULT_SERVICE_REGION."
  type        = string
  default     = null
}

variable "cors_allowed_origins" {
  description = "CORS allow_origins for the HTTP API and the Lambda's response echo."
  type        = list(string)
  default = [
    "https://demo.ms3dm.tech",
    "http://localhost:5173",
  ]
}

variable "rate_limit_per_5min" {
  description = "Per-IP rate limit for the WAF rate-based rule. Default 100 (matches ADR-010 §5)."
  type        = number
  default     = 100
}

variable "log_level" {
  description = "Log level for the Lambda runtime."
  type        = string
  default     = "INFO"
}
