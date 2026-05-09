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

# ---- Phase 3.a — public web demo ----

output "api_endpoint" {
  description = "API Gateway HTTP API endpoint. SPA POSTs to <endpoint>/ask."
  value       = module.gateway.api_endpoint
}

output "gateway_lambda_function_name" {
  description = "Gateway Lambda function name. Phase 3.b's Slack adapter invokes this directly via Lambda Invoke."
  value       = module.gateway.lambda_function_name
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID."
  value       = module.auth.user_pool_id
}

output "cognito_user_pool_client_id" {
  description = "Cognito user pool client ID for the SPA."
  value       = module.auth.user_pool_client_id
}

output "cognito_hosted_ui_domain" {
  description = "Cognito Hosted UI fully qualified domain."
  value       = module.auth.hosted_ui_domain
}

output "cognito_jwt_issuer_url" {
  description = "JWT issuer URL feeding the API Gateway authorizer."
  value       = module.auth.jwt_issuer_url
}

output "web_bucket_name" {
  description = "S3 origin bucket for the SPA bundle. CI consumes this via the /gagent/<env>/web_bucket_name SSM parameter."
  value       = module.web_demo.bucket_name
}

output "web_distribution_id" {
  description = "CloudFront distribution ID for the SPA. CI consumes this via SSM."
  value       = module.web_demo.distribution_id
}

output "web_distribution_domain_name" {
  description = "CloudFront distribution domain (e.g. dxxxxxx.cloudfront.net). The CNAME target the operator points demo.ms3dm.tech at (§5.8)."
  value       = module.web_demo.distribution_domain_name
}

output "acm_certificate_validation_records" {
  description = "DNS records the operator must add at IONOS during the first apply to validate the ACM cert. Empty after validation completes."
  value       = module.web_demo.acm_certificate_validation_records
}

output "phase_3a_ssm_parameter_names" {
  description = "Sorted list of SSM parameter names under /gagent/<env>/ that the §15 CI workflow and the operator consume."
  value       = sort([for k, _ in local.phase_3a_ssm_params : "/gagent/${local.env}/${k}"])
}

output "cost_alarm_sns_topic_arn" {
  description = "SNS topic ARN that fans out the ADR-013 §5.2 Bedrock cost-alarm transitions. Subscribe additional endpoints (Slack, PagerDuty) here for richer notification."
  value       = module.cost_guardrails.sns_topic_arn
}
