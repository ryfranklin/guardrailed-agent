output "invocation_log_group" {
  description = "Name of the CloudWatch log group that gagent_client.emit_invocation_log writes structured trace JSON to."
  value       = aws_cloudwatch_log_group.invocations.name
}

output "invocation_log_group_arn" {
  description = "ARN of the gagent invocation log group."
  value       = aws_cloudwatch_log_group.invocations.arn
}

output "invocations_write_policy_json" {
  description = "IAM policy document granting CreateLogStream + PutLogEvents on the invocation log group. Attach to every persona role + Lambda role that needs to emit traces."
  value       = data.aws_iam_policy_document.invocations_write.json
}

output "invocations_read_policy_json" {
  description = "IAM policy document granting Logs Insights query access. Attach to operator / read-only roles that consume recent_traces / audit_trace."
  value       = data.aws_iam_policy_document.invocations_read.json
}
