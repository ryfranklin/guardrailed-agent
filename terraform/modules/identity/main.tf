terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id       = data.aws_caller_identity.current.account_id
  partition        = data.aws_partition.current.partition
  region           = data.aws_region.current.name
  athena_wg_arn    = "arn:${local.partition}:athena:${local.region}:${local.account_id}:workgroup/${var.athena_workgroup_name}"
  glue_db_arn      = "arn:${local.partition}:glue:${local.region}:${local.account_id}:database/${var.glue_database_name}"
  glue_table_arn   = "arn:${local.partition}:glue:${local.region}:${local.account_id}:table/${var.glue_database_name}/*"
  glue_catalog_arn = "arn:${local.partition}:glue:${local.region}:${local.account_id}:catalog"
}

data "aws_iam_policy_document" "persona_trust_analyst" {
  statement {
    sid     = "AllowAssumeWithRoleTagAnalyst"
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_assumer_arns
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/role"
      values   = ["analyst"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["role"]
    }
  }
}

data "aws_iam_policy_document" "persona_trust_regional_manager" {
  statement {
    sid     = "AllowAssumeWithRoleTagRegionalManager"
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_assumer_arns
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/role"
      values   = ["regional_manager"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["role", "region"]
    }

    condition {
      test     = "StringLike"
      variable = "aws:RequestTag/region"
      values   = ["*"]
    }
  }
}

data "aws_iam_policy_document" "persona_trust_admin" {
  statement {
    sid     = "AllowAssumeWithRoleTagAdmin"
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_assumer_arns
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/role"
      values   = ["admin"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["role"]
    }
  }
}

data "aws_iam_policy_document" "data_access" {
  statement {
    sid    = "AthenaQuery"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetQueryResultsStream",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
      "athena:ListQueryExecutions",
    ]
    resources = [local.athena_wg_arn]
  }

  statement {
    sid    = "GlueCatalogRead"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
    ]
    resources = [
      local.glue_catalog_arn,
      local.glue_db_arn,
      local.glue_table_arn,
    ]
  }

  statement {
    sid       = "LakeFormationGetDataAccess"
    effect    = "Allow"
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }

  statement {
    sid    = "S3DataRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = concat(
      var.data_bucket_arns,
      [for arn in var.data_bucket_arns : "${arn}/*"],
    )
  }

  statement {
    sid    = "S3AthenaResultsWrite"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = [for arn in var.data_bucket_arns : "${arn}/*"]
  }
}

resource "aws_iam_role" "analyst" {
  name               = "${var.role_name_prefix}analyst-${var.env}"
  description        = "Analyst persona — column-masked PII via Lake Formation LF-Tag policy."
  assume_role_policy = data.aws_iam_policy_document.persona_trust_analyst.json
  tags               = var.tags
}

resource "aws_iam_role" "regional_manager" {
  name               = "${var.role_name_prefix}regional-manager-${var.env}"
  description        = "RegionalManager persona — full PII for assigned region only."
  assume_role_policy = data.aws_iam_policy_document.persona_trust_regional_manager.json
  tags               = var.tags
}

resource "aws_iam_role" "admin" {
  name               = "${var.role_name_prefix}admin-${var.env}"
  description        = "Admin persona — unrestricted access."
  assume_role_policy = data.aws_iam_policy_document.persona_trust_admin.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "analyst_data_access" {
  name   = "data-access"
  role   = aws_iam_role.analyst.id
  policy = data.aws_iam_policy_document.data_access.json
}

resource "aws_iam_role_policy" "regional_manager_data_access" {
  name   = "data-access"
  role   = aws_iam_role.regional_manager.id
  policy = data.aws_iam_policy_document.data_access.json
}

resource "aws_iam_role_policy" "admin_data_access" {
  name   = "data-access"
  role   = aws_iam_role.admin.id
  policy = data.aws_iam_policy_document.data_access.json
}
