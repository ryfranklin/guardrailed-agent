variable "env" {
  description = "Environment name (e.g., demo). Used in guardrail naming."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for the guardrail name. Final name is <prefix><env>."
  type        = string
  default     = "gagent-"
}

variable "pii_action" {
  description = <<-EOT
    Action applied to detected PII entities.
      - NONE    — skip Bedrock-side PII handling entirely. Lake Formation is the data-access gate; this avoids double-anonymization that would hide legitimate Admin access.
      - ANONYMIZE — replace detected PII with placeholders in both directions. Belt-and-suspenders; can over-mask normal text (e.g. US state codes flagged as ADDRESS).
      - BLOCK   — reject the entire response if any PII is detected. Reserved for client-specific severe cases.
    Default NONE for the flagship demo because the demo moment requires Admin to see real PII; clients can opt up.
  EOT
  type        = string
  default     = "NONE"
  validation {
    condition     = contains(["NONE", "ANONYMIZE", "BLOCK"], var.pii_action)
    error_message = "pii_action must be NONE, ANONYMIZE, or BLOCK."
  }
}

variable "denied_topics" {
  description = "Off-scope topics to deny. Each entry needs name, definition, and optional examples."
  type = list(object({
    name       = string
    definition = string
    examples   = optional(list(string), [])
  }))
  default = [
    {
      name       = "LegalAdvice"
      definition = "Any request for legal advice, interpretation of statutes, or guidance on regulatory compliance. The agent is an analytics assistant, not a lawyer."
      examples = [
        "What's the best way to structure my LLC for tax purposes?",
        "Am I allowed to fire someone for this reason?",
      ]
    },
    {
      name       = "MedicalAdvice"
      definition = "Any request for medical, psychiatric, or therapeutic guidance. The agent does not have clinical training."
      examples = [
        "What medication should I take for my migraine?",
        "Is this rash dangerous?",
      ]
    },
  ]
}

variable "grounding_threshold" {
  description = "Contextual grounding threshold. Higher reduces hallucination but may over-reject correct answers. 0.7 is a defensible default for analytics."
  type        = number
  default     = 0.7
  validation {
    condition     = var.grounding_threshold >= 0 && var.grounding_threshold <= 1
    error_message = "grounding_threshold must be between 0 and 1."
  }
}

variable "relevance_threshold" {
  description = "Contextual grounding relevance threshold."
  type        = number
  default     = 0.7
  validation {
    condition     = var.relevance_threshold >= 0 && var.relevance_threshold <= 1
    error_message = "relevance_threshold must be between 0 and 1."
  }
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}
