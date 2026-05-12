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
  function_name = "${var.name_prefix}governed-query-${var.env}"
  zip_path      = "${path.module}/.build/${var.env}.zip"
  account_id    = data.aws_caller_identity.current.account_id
  region        = data.aws_region.current.name
  partition     = data.aws_partition.current.partition
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
    sid    = "EmitInvocationTrace"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      var.invocation_log_group_arn,
      "${var.invocation_log_group_arn}:*",
    ]
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.function_name}-exec"
  description        = "Execution role for the governed_query Lambda. Assumes persona roles to inherit session tags."
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

resource "aws_lambda_function" "governed_query" {
  function_name    = local.function_name
  description      = "Bedrock Agent action group: governed query templates over the HVAC home-services schema via Athena (ADR-008 §4 step 6). Six tools dispatched by apiPath."
  role             = aws_iam_role.lambda.arn
  handler          = "handler.handler"
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      GLUE_DATABASE    = var.glue_database_name
      ATHENA_WORKGROUP = var.athena_workgroup_name
      GAGENT_LOG_GROUP = var.invocation_log_group
      ENV              = var.env
    }
  }

  tags = var.tags

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_permission" "bedrock_invoke" {
  statement_id  = "AllowBedrockAgentInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.governed_query.function_name
  principal     = "bedrock.amazonaws.com"
  source_arn    = "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:agent/*"
}

locals {
  # Shared response object schema referenced by every tool path below.
  _common_response_schema = {
    type = "object"
    properties = {
      rows            = { type = "array", items = { type = "object" } }
      row_count       = { type = "integer" }
      template        = { type = "string" }
      persona         = { type = "string" }
      question_intent = { type = "string" }
    }
  }

  # Response status codes shared across every tool path.
  _common_responses = {
    "200" = {
      description = "Rows from the governed dataset. PII columns may be NULL or 'REDACTED' depending on the caller's persona; sensitivity=high columns are Owner-only."
      content     = { "application/json" = { schema = local._common_response_schema } }
    }
    "400" = { description = "Invalid request — unknown filter, malformed parameter, or include_deleted requested by a non-Owner persona." }
    "403" = { description = "Lake Formation denied access for the caller's persona." }
  }

  # Common parameter sub-schemas (limit, question_intent, filters object).
  _common_request_properties = {
    question_intent = {
      type        = "string"
      description = "Short natural-language summary of what the caller is asking. Used for trace context."
    }
    limit = {
      type        = "integer"
      minimum     = 1
      maximum     = 200
      default     = 15
      description = "Maximum rows to return. Default 15 keeps the response inside the 30s API Gateway window; the agent should ask for more (up to 200) only when the user explicitly requests it."
    }
  }

  # Schema for an SCD2-aware tool: adds optional as_of_date.
  _scd2_request_property = {
    as_of_date = {
      type        = "string"
      pattern     = "^\\d{4}-\\d{2}-\\d{2}$"
      description = "Optional point-in-time lookup (YYYY-MM-DD). Switches the SCD2 predicate from is_current = TRUE to effective_from <= as_of_date < effective_to."
    }
  }

  # Schema for a soft-delete tool: adds optional include_deleted (Owner only).
  _soft_delete_request_property = {
    include_deleted = {
      type        = "boolean"
      default     = false
      description = "If true, include rows where deleted_at IS NOT NULL. Owner persona only — the Lambda rejects this parameter for any other persona."
    }
  }

  openapi_schema = jsonencode({
    openapi = "3.0.0"
    info = {
      title       = "Governed HVAC Dataset Query Templates"
      version     = "1.0.0"
      description = "Six SQL templates over the ADR-008 HVAC schema, dispatched by path. Lake Formation enforces row and column visibility on every call. SCD2 dimensions default to is_current = TRUE; soft-delete facts default to deleted_at IS NULL. Treat NULL PII fields as redacted — do not infer."
    }
    paths = {
      "/customers" = {
        post = {
          operationId = "queryCustomers"
          summary     = "Query the governed customer SCD2 dimension."
          description = "Returns customer rows. Defaults to current SCD2 versions (is_current = TRUE). Supply as_of_date for point-in-time lookups."
          requestBody = {
            required = true
            content = {
              "application/json" = {
                schema = {
                  type     = "object"
                  required = ["question_intent"]
                  properties = merge(
                    local._common_request_properties,
                    local._scd2_request_property,
                    {
                      filters = {
                        type        = "object"
                        description = "Equality filters. Allowed keys: customer_id, customer_type, service_tier, service_region, city, postal_code."
                      }
                    },
                  )
                }
              }
            }
          }
          responses = local._common_responses
        }
      }
      "/jobs" = {
        post = {
          operationId = "queryJobs"
          summary     = "Query service_job (soft-delete fact)."
          description = "Returns service_job rows. Defaults to deleted_at IS NULL. Owner persona may set include_deleted=true."
          requestBody = {
            required = true
            content = {
              "application/json" = {
                schema = {
                  type     = "object"
                  required = ["question_intent"]
                  properties = merge(
                    local._common_request_properties,
                    local._soft_delete_request_property,
                    {
                      filters = {
                        type        = "object"
                        description = "Equality keys: job_id, customer_id, technician_id, equipment_id, status, job_type. Range keys: scheduled_date_from, scheduled_date_to, completed_date_from, completed_date_to (YYYY-MM-DD)."
                      }
                    },
                  )
                }
              }
            }
          }
          responses = local._common_responses
        }
      }
      "/signals" = {
        post = {
          operationId = "querySignals"
          summary     = "Query customer_signal_daily (engagement, churn risk, next-best-action)."
          description = "Daily customer signal rollup. Bring date_from/date_to to bound the window."
          requestBody = {
            required = true
            content = {
              "application/json" = {
                schema = {
                  type     = "object"
                  required = ["question_intent"]
                  properties = merge(
                    local._common_request_properties,
                    {
                      filters = {
                        type        = "object"
                        description = "Equality keys: customer_id, next_best_action. Range keys: signal_date_from, signal_date_to (YYYY-MM-DD)."
                      }
                    },
                  )
                }
              }
            }
          }
          responses = local._common_responses
        }
      }
      "/equipment_telemetry" = {
        post = {
          operationId = "queryEquipmentTelemetry"
          summary     = "Query equipment_telemetry_daily (synthetic predictive-maintenance score; ADR-008 open items)."
          description = "Daily equipment telemetry with cycle_count, fault_code_count, efficiency_index, and predicted_failure_30d. The score is synthetic — see ADR-008."
          requestBody = {
            required = true
            content = {
              "application/json" = {
                schema = {
                  type     = "object"
                  required = ["question_intent"]
                  properties = merge(
                    local._common_request_properties,
                    {
                      filters = {
                        type        = "object"
                        description = "Equality keys: equipment_id. Range keys: telemetry_date_from, telemetry_date_to (YYYY-MM-DD), min_predicted_failure_30d (0.0-1.0)."
                      }
                    },
                  )
                }
              }
            }
          }
          responses = local._common_responses
        }
      }
      "/technician_utilization" = {
        post = {
          operationId = "queryTechnicianUtilization"
          summary     = "Query technician_utilization_daily (revenue, billable hours, parts cost)."
          description = "Daily technician utilization. revenue_generated_usd and parts_consumed_cost_usd are sensitivity=high — Owner-only via Lake Formation."
          requestBody = {
            required = true
            content = {
              "application/json" = {
                schema = {
                  type     = "object"
                  required = ["question_intent"]
                  properties = merge(
                    local._common_request_properties,
                    {
                      filters = {
                        type        = "object"
                        description = "Equality keys: technician_id. Range keys: utilization_date_from, utilization_date_to (YYYY-MM-DD)."
                      }
                    },
                  )
                }
              }
            }
          }
          responses = local._common_responses
        }
      }
      "/truck_rolls" = {
        post = {
          operationId = "queryTruckRolls"
          summary     = "Query truck_roll (soft-delete fact joining service_job × technician × parts_inventory × equipment)."
          description = "Returns truck_roll rows. Defaults to deleted_at IS NULL. Owner persona may set include_deleted=true."
          requestBody = {
            required = true
            content = {
              "application/json" = {
                schema = {
                  type     = "object"
                  required = ["question_intent"]
                  properties = merge(
                    local._common_request_properties,
                    local._soft_delete_request_property,
                    {
                      filters = {
                        type        = "object"
                        description = "Equality keys: truck_roll_id, job_id, technician_id, equipment_id, outcome. Range keys: dispatch_ts_from, dispatch_ts_to (timestamp)."
                      }
                    },
                  )
                }
              }
            }
          }
          responses = local._common_responses
        }
      }
    }
  })
}

resource "aws_s3_object" "openapi_schema" {
  bucket  = element(split(":", var.athena_results_bucket_arn), 5)
  key     = "schemas/${var.env}/governed_query.openapi.json"
  content = local.openapi_schema
  etag    = md5(local.openapi_schema)
}
