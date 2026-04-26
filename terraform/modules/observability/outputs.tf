output "langfuse_secret_arn" {
  description = "ARN of the Secrets Manager secret holding Langfuse credentials."
  value       = aws_secretsmanager_secret.langfuse.arn
}

output "langfuse_secret_name" {
  description = "Name of the Langfuse secret."
  value       = aws_secretsmanager_secret.langfuse.name
}

output "langfuse_host" {
  description = "Langfuse host URL — exposed for the eval runner and invoke CLI to read alongside the secret."
  value       = var.langfuse_host
}

output "langfuse_read_policy_json" {
  description = "IAM policy document granting GetSecretValue on the Langfuse secret. Attach to any caller that needs to read it."
  value       = data.aws_iam_policy_document.langfuse_read.json
}
