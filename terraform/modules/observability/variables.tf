variable "env" {
  description = "Environment name (e.g., demo). Used in secret naming."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for the Secrets Manager secret name."
  type        = string
  default     = "gagent/"
}

variable "langfuse_host" {
  description = "Langfuse host URL. Default cloud.langfuse.com per §16."
  type        = string
  default     = "https://cloud.langfuse.com"
}

variable "langfuse_public_key" {
  description = "Langfuse public key. Stored in Secrets Manager."
  type        = string
  sensitive   = true
}

variable "langfuse_secret_key" {
  description = "Langfuse secret key. Stored in Secrets Manager."
  type        = string
  sensitive   = true
}

variable "recovery_window_in_days" {
  description = "Recovery window for the secret. 0 deletes immediately on destroy — useful for dev/demo, never for production clients."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}
