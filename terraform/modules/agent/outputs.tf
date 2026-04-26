output "agent_id" {
  description = "Bedrock Agent ID."
  value       = aws_bedrockagent_agent.this.agent_id
}

output "agent_arn" {
  description = "Bedrock Agent ARN."
  value       = aws_bedrockagent_agent.this.agent_arn
}

output "agent_name" {
  description = "Bedrock Agent name."
  value       = aws_bedrockagent_agent.this.agent_name
}

output "agent_alias_id" {
  description = "Live alias ID. Pass to InvokeAgent."
  value       = aws_bedrockagent_agent_alias.live.agent_alias_id
}

output "agent_alias_arn" {
  description = "Live alias ARN."
  value       = aws_bedrockagent_agent_alias.live.agent_alias_arn
}

output "agent_resource_role_arn" {
  description = "Agent execution role ARN. Useful for cross-module audit references."
  value       = aws_iam_role.agent.arn
}
