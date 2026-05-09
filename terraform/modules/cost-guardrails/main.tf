terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  topic_name           = "${var.name_prefix}bedrock-cost-alarms-${var.env}"
  warn_alarm_name      = "${var.name_prefix}bedrock-cost-warn-${var.env}"
  hard_stop_alarm_name = "${var.name_prefix}bedrock-cost-hard-stop-${var.env}"
}

resource "aws_sns_topic" "bedrock_cost_alarms" {
  name              = local.topic_name
  display_name      = "Bedrock cost alarms (${var.env})"
  kms_master_key_id = "alias/aws/sns"
  tags              = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.bedrock_cost_alarms.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

resource "aws_cloudwatch_metric_alarm" "bedrock_warn" {
  alarm_name          = local.warn_alarm_name
  alarm_description   = "ADR-013 §5.2 warn tier. Bedrock estimated charges crossed ${var.warn_threshold_usd} USD/day. Runbook: docs/abuse-response.md §1."
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.warn_threshold_usd
  treat_missing_data  = "notBreaching"

  dimensions = {
    Currency    = "USD"
    ServiceName = "AmazonBedrock"
  }

  alarm_actions = [aws_sns_topic.bedrock_cost_alarms.arn]
  ok_actions    = [aws_sns_topic.bedrock_cost_alarms.arn]

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "bedrock_hard_stop" {
  alarm_name          = local.hard_stop_alarm_name
  alarm_description   = "ADR-013 §5.2 hard-stop tier. Bedrock estimated charges crossed ${var.hard_stop_threshold_usd} USD/day. Runbook: docs/abuse-response.md §2 — immediate containment."
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.hard_stop_threshold_usd
  treat_missing_data  = "notBreaching"

  dimensions = {
    Currency    = "USD"
    ServiceName = "AmazonBedrock"
  }

  alarm_actions = [aws_sns_topic.bedrock_cost_alarms.arn]
  ok_actions    = [aws_sns_topic.bedrock_cost_alarms.arn]

  tags = var.tags
}
