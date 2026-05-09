output "sns_topic_arn" {
  description = "ARN of the SNS topic that fires when either alarm transitions to ALARM. Subscribe additional endpoints (Slack, PagerDuty, on-call lambda) to this topic in client deployments."
  value       = aws_sns_topic.bedrock_cost_alarms.arn
}

output "warn_alarm_arn" {
  description = "ARN of the warn-tier CloudWatch alarm. Surface for cross-stack references; the alarm action is already wired to the SNS topic."
  value       = aws_cloudwatch_metric_alarm.bedrock_warn.arn
}

output "hard_stop_alarm_arn" {
  description = "ARN of the hard-stop-tier CloudWatch alarm. Surface for cross-stack references."
  value       = aws_cloudwatch_metric_alarm.bedrock_hard_stop.arn
}
