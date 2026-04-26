terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  function_name = "${var.name_prefix}query-ambassadors-${var.env}"
  build_dir     = "${path.module}/.build/${var.env}"
  zip_path      = "${path.module}/.build/${var.env}.zip"
  account_id    = data.aws_caller_identity.current.account_id
  region        = data.aws_region.current.name
  partition     = data.aws_partition.current.partition
  athena_wg_arn = "arn:${local.partition}:athena:${local.region}:${local.account_id}:workgroup/${var.athena_workgroup_name}"
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = var.lambda_source_dir
  output_path = local.zip_path
  excludes    = ["tests", "__pycache__", "*.pyc", "README.md", "requirements.txt"]
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_policy" {
  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.function_name}*"]
  }

  statement {
    sid    = "AssumePersonaRoles"
    effect = "Allow"
    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]
    resources = var.persona_role_arns
  }

  statement {
    sid       = "ReadLangfuseSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.langfuse_secret_arn]
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.function_name}-exec"
  description        = "Execution role for the query_ambassadors Lambda. Assumes persona roles to inherit session tags."
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "lambda" {
  name   = "lambda-policy"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_policy.json
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "query_ambassadors" {
  function_name    = local.function_name
  description      = "Bedrock Agent action group: queries the governed ambassador dataset via Athena under an assumed persona role."
  role             = aws_iam_role.lambda.arn
  handler          = "handler.handler"
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      GLUE_DATABASE       = var.glue_database_name
      ATHENA_WORKGROUP    = var.athena_workgroup_name
      LANGFUSE_SECRET_ARN = var.langfuse_secret_arn
      ENV                 = var.env
    }
  }

  tags = var.tags

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_permission" "bedrock_invoke" {
  statement_id  = "AllowBedrockAgentInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query_ambassadors.function_name
  principal     = "bedrock.amazonaws.com"
  source_arn    = "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:agent/*"
}

locals {
  openapi_schema = jsonencode({
    openapi = "3.0.0"
    info = {
      title       = "Ambassador Dataset Query"
      version     = "1.0.0"
      description = "Query the governed ambassador dataset. Lake Formation enforces per-persona row and column visibility on every call."
    }
    paths = {
      "/query" = {
        post = {
          operationId = "queryAmbassadors"
          summary     = "Query ambassador, team, order, or signal data."
          description = "Returns rows from the governed dataset. Columns and rows are filtered by Lake Formation based on the calling principal's session tags. Treat any 'REDACTED' or null PII columns as redacted — do not infer or guess."
          requestBody = {
            required = true
            content = {
              "application/json" = {
                schema = {
                  type     = "object"
                  required = ["question_intent"]
                  properties = {
                    question_intent = {
                      type        = "string"
                      description = "Short natural-language summary of what the caller is asking. Used for trace context."
                    }
                    table = {
                      type        = "string"
                      enum        = ["ambassador", "ambassador_team", "order_fact", "signal_fact"]
                      description = "Which governed table to query. Pick exactly one."
                    }
                    filters = {
                      type        = "object"
                      description = <<-EOT
                        Optional equality filters as { column: value }. Multiple filters are AND-combined. Values are case-sensitive.

                        Known value casing:
                          - status: lowercase ('active', 'inactive', 'terminated')
                          - rank: lowercase ('bronze', 'silver', 'gold', 'platinum', 'diamond')
                          - region: uppercase 2-letter US state code ('CA', 'NY', 'FL', etc.)
                          - product_category: lowercase ('wellness', 'beauty', 'home', 'apparel', 'outdoor')
                          - order_status: lowercase ('completed', 'refunded', 'cancelled')
                          - next_best_action: lowercase ('outreach', 'coaching', 'promotion', 'retention_offer')
                      EOT
                      additionalProperties = {
                        oneOf = [
                          { type = "string" },
                          { type = "number" },
                          { type = "integer" },
                          { type = "boolean" },
                        ]
                      }
                    }
                    limit = {
                      type        = "integer"
                      minimum     = 1
                      maximum     = 200
                      default     = 50
                      description = "Maximum rows to return. Hard cap at 200."
                    }
                  }
                }
              }
            }
          }
          responses = {
            "200" = {
              description = "Rows from the governed dataset. PII columns may be 'REDACTED' depending on the caller's role."
              content = {
                "application/json" = {
                  schema = {
                    type = "object"
                    properties = {
                      rows = {
                        type  = "array"
                        items = { type = "object" }
                      }
                      row_count = { type = "integer" }
                      table     = { type = "string" }
                      persona   = { type = "string" }
                    }
                  }
                }
              }
            }
            "400" = { description = "Invalid request — unknown table or unsupported filter." }
            "403" = { description = "Lake Formation denied access for the caller's persona." }
          }
        }
      }
    }
  })
}

resource "aws_s3_object" "openapi_schema" {
  bucket  = element(split(":", var.athena_results_bucket_arn), 5)
  key     = "schemas/${var.env}/query_ambassadors.openapi.json"
  content = local.openapi_schema
  etag    = md5(local.openapi_schema)
}
