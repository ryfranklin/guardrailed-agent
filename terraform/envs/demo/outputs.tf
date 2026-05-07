output "dispatcher_role_arn" {
  description = "Dispatcher persona role ARN — pass to invoke-agent.py via --assume-role."
  value       = module.identity.dispatcher_role_arn
}

output "technician_lead_role_arn" {
  description = "TechnicianLead persona role ARN."
  value       = module.identity.technician_lead_role_arn
}

output "owner_role_arn" {
  description = "Owner persona role ARN."
  value       = module.identity.owner_role_arn
}

output "agent_id" {
  description = "Bedrock Agent ID."
  value       = module.agent.agent_id
}

output "agent_alias_id" {
  description = "Bedrock Agent live alias ID — pass to InvokeAgent."
  value       = module.agent.agent_alias_id
}

output "guardrail_id" {
  description = "Bedrock Guardrail ID."
  value       = module.guardrails.guardrail_id
}

output "data_bucket_name" {
  description = "Raw data bucket name. The synthesizer writes Iceberg metadata + Parquet here."
  value       = module.data_plane.data_bucket_name
}

output "athena_workgroup_name" {
  description = "Athena workgroup the synthesizer and Lambda use."
  value       = module.data_plane.athena_workgroup_name
}

output "glue_database_name" {
  description = "Glue catalog database holding the governed tables (ADR-008)."
  value       = module.data_plane.glue_database_name
}

output "invocation_log_group" {
  description = "CloudWatch log group that gagent_client.emit_invocation_log writes structured trace JSON to. AgentCore Observability surfaces this in the CloudWatch console."
  value       = module.observability.invocation_log_group
}

output "invocation_log_group_arn" {
  description = "ARN of the gagent invocation log group."
  value       = module.observability.invocation_log_group_arn
}

output "invocations_read_policy_json" {
  description = "IAM policy document granting Logs Insights access on the invocation log group. Attach to operator / MCP / CLI principals that consume recent_traces / audit_trace."
  value       = module.observability.invocations_read_policy_json
}

output "lambda_function_name" {
  description = "Lambda action group function name."
  value       = module.tools.lambda_function_name
}

output "mcp_governance_reader_policy_arn" {
  description = "ARN of the read-only IAM policy for the MCP server's governance probe tools (ADR-009 Phase 2.b). Attach to the MCP-runner principal in client deployments where the operator is not admin."
  value       = aws_iam_policy.mcp_governance_reader.arn
}
