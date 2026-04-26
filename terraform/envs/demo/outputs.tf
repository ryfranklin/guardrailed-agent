output "analyst_role_arn" {
  description = "Analyst persona role ARN — pass to invoke-agent.py via --assume-role."
  value       = module.identity.analyst_role_arn
}

output "regional_manager_role_arn" {
  description = "RegionalManager persona role ARN."
  value       = module.identity.regional_manager_role_arn
}

output "admin_role_arn" {
  description = "Admin persona role ARN."
  value       = module.identity.admin_role_arn
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
  description = "Glue catalog database holding the four governed tables."
  value       = module.data_plane.glue_database_name
}

output "langfuse_secret_arn" {
  description = "Secrets Manager ARN for Langfuse credentials."
  value       = module.observability.langfuse_secret_arn
}

output "langfuse_host" {
  description = "Langfuse host URL."
  value       = module.observability.langfuse_host
}

output "lambda_function_name" {
  description = "Lambda action group function name."
  value       = module.tools.lambda_function_name
}
