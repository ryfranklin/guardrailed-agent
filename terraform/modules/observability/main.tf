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
  secret_name = "${var.name_prefix}${var.env}/langfuse"
}

resource "aws_secretsmanager_secret" "langfuse" {
  name                    = local.secret_name
  description             = "Langfuse credentials for the ${var.env} guardrailed-agent. Read by the Lambda action group, eval runner, and invoke-agent CLI."
  recovery_window_in_days = var.recovery_window_in_days
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "langfuse" {
  secret_id = aws_secretsmanager_secret.langfuse.id
  secret_string = jsonencode({
    host       = var.langfuse_host
    public_key = var.langfuse_public_key
    secret_key = var.langfuse_secret_key
  })
}

data "aws_iam_policy_document" "langfuse_read" {
  statement {
    sid       = "ReadLangfuseSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.langfuse.arn]
  }
}
