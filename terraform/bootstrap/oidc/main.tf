terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "guardrailed-agent"
      ManagedBy = "terraform-bootstrap-oidc"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  oidc_url   = "token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://${local.oidc_url}"

  client_id_list = ["sts.amazonaws.com"]

  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

data "aws_iam_policy_document" "ci_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "${local.oidc_url}:sub"
      values = [
        for ref in var.allowed_refs :
        "repo:${var.github_owner}/${var.github_repo}:${ref}"
      ]
    }
  }
}

data "aws_iam_policy_document" "ci_permissions" {
  statement {
    sid    = "TerraformPlanReadOnly"
    effect = "Allow"
    actions = [
      "iam:Get*",
      "iam:List*",
      "s3:GetBucket*",
      "s3:ListBucket",
      "s3:ListAllMyBuckets",
      "lambda:Get*",
      "lambda:List*",
      "bedrock:Get*",
      "bedrock:List*",
      "bedrock-agent:Get*",
      "bedrock-agent:List*",
      "athena:Get*",
      "athena:List*",
      "glue:Get*",
      "glue:List*",
      "lakeformation:Get*",
      "lakeformation:List*",
      "lakeformation:Describe*",
      "secretsmanager:DescribeSecret",
      "secretsmanager:ListSecrets",
      "logs:Describe*",
      "cloudwatch:Get*",
      "cloudwatch:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "TerraformStateBackend"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]
    resources = var.tfstate_bucket_arn != "" ? [var.tfstate_bucket_arn, "${var.tfstate_bucket_arn}/*"] : ["*"]
  }

  statement {
    sid    = "TerraformStateLock"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = var.tfstate_lock_table_arn != "" ? [var.tfstate_lock_table_arn] : ["*"]
  }

  statement {
    sid    = "EvalInvokeAgent"
    effect = "Allow"
    actions = [
      "bedrock:InvokeAgent",
      "bedrock-agent-runtime:InvokeAgent",
      "bedrock:ApplyGuardrail",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "EvalAssumePersonaRoles"
    effect = "Allow"
    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]
    resources = [
      "arn:${local.partition}:iam::${local.account_id}:role/gagent-analyst-*",
      "arn:${local.partition}:iam::${local.account_id}:role/gagent-regional-manager-*",
      "arn:${local.partition}:iam::${local.account_id}:role/gagent-admin-*",
    ]
  }

  statement {
    sid    = "EvalReadLangfuseSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      "arn:${local.partition}:secretsmanager:*:${local.account_id}:secret:gagent/*",
    ]
  }
}

resource "aws_iam_role" "ci" {
  name               = var.role_name
  description        = "CI role for ${var.github_owner}/${var.github_repo} via GitHub OIDC."
  assume_role_policy = data.aws_iam_policy_document.ci_trust.json
}

resource "aws_iam_role_policy" "ci" {
  name   = "ci-policy"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.ci_permissions.json
}
