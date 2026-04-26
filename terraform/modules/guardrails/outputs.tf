output "guardrail_id" {
  description = "Bedrock Guardrail ID — pass to the agent module."
  value       = aws_bedrock_guardrail.this.guardrail_id
}

output "guardrail_arn" {
  description = "Bedrock Guardrail ARN."
  value       = aws_bedrock_guardrail.this.guardrail_arn
}

output "guardrail_version" {
  description = "Pinned Guardrail version. Bedrock Agents accept either a version number or DRAFT; we pin to a published version for reproducibility."
  value       = aws_bedrock_guardrail_version.this.version
}

output "guardrail_name" {
  description = "Guardrail name for human-readable references."
  value       = aws_bedrock_guardrail.this.name
}
