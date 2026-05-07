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
  log_group_name = var.log_group_name
}

resource "aws_cloudwatch_log_group" "invocations" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

data "aws_iam_policy_document" "invocations_write" {
  statement {
    sid    = "WriteInvocationLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      aws_cloudwatch_log_group.invocations.arn,
      "${aws_cloudwatch_log_group.invocations.arn}:*",
    ]
  }
}

data "aws_iam_policy_document" "invocations_read" {
  statement {
    sid    = "ReadInvocationLogs"
    effect = "Allow"
    actions = [
      "logs:StartQuery",
      "logs:StopQuery",
      "logs:GetQueryResults",
      "logs:DescribeQueries",
      "logs:GetLogEvents",
      "logs:FilterLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      aws_cloudwatch_log_group.invocations.arn,
      "${aws_cloudwatch_log_group.invocations.arn}:*",
    ]
  }
}
