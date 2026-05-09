locals {
  function_name = "${var.name_prefix}gateway-${var.env}"
  build_root    = "${path.module}/.build"
  build_src_dir = "${local.build_root}/${var.env}/src"
  zip_path      = "${local.build_root}/${var.env}.zip"

  # Hashes used to drive the staging build. Re-stage when any handler file
  # or any gagent_client file changes.
  handler_files = fileset(var.lambda_source_dir, "**/*.py")
  client_files  = fileset(var.gagent_client_source_dir, "**/*.py")
  client_data_files = fileset(
    var.gagent_client_source_dir, "**/*.json",
  )

  handler_hash = sha256(join("", [
    for f in local.handler_files :
    filemd5("${var.lambda_source_dir}/${f}")
  ]))
  client_hash = sha256(join("", concat(
    [for f in local.client_files : filemd5("${var.gagent_client_source_dir}/${f}")],
    [for f in local.client_data_files : filemd5("${var.gagent_client_source_dir}/${f}")],
  )))
}

# Stage the Lambda build. The gateway handler imports `gagent_client`, so the
# package must sit alongside handler.py inside the zip. archive_file does not
# support multiple source dirs, so a build directory is assembled here and
# archived in one shot.
resource "terraform_data" "lambda_build" {
  triggers_replace = {
    handler_hash = local.handler_hash
    client_hash  = local.client_hash
    build_src    = local.build_src_dir
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-eu", "-c"]
    command     = <<-EOT
      rm -rf "${local.build_src_dir}"
      mkdir -p "${local.build_src_dir}"
      cp -R "${var.lambda_source_dir}/." "${local.build_src_dir}/"
      cp -R "${var.gagent_client_source_dir}" "${local.build_src_dir}/gagent_client"
    EOT
  }
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = local.build_src_dir
  output_path = local.zip_path
  excludes    = ["tests", "__pycache__", "*.pyc", "README.md", "requirements.txt"]

  depends_on = [terraform_data.lambda_build]
}

# ---- IAM ----

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
    sid    = "AssumePersonaRoles"
    effect = "Allow"
    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]
    resources = values(var.persona_role_arns)
  }

  statement {
    sid       = "InvokeAgent"
    effect    = "Allow"
    actions   = ["bedrock:InvokeAgent"]
    resources = [var.agent_alias_arn]
  }

  statement {
    sid       = "InvokeGovernedQuery"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [var.governed_query_lambda_arn]
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

  statement {
    sid    = "LambdaOwnLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.lambda.arn}",
      "${aws_cloudwatch_log_group.lambda.arn}:*",
      "${aws_cloudwatch_log_group.access.arn}",
      "${aws_cloudwatch_log_group.access.arn}:*",
    ]
  }

  statement {
    sid    = "XRay"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.function_name}-exec"
  description        = "Execution role for the gateway Lambda. Assumes persona roles to inherit session tags before invoking Bedrock Agent."
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

resource "aws_lambda_function" "gateway" {
  function_name    = local.function_name
  description      = "Gateway Lambda behind the API Gateway HTTP API for the public web demo. Resolves persona via CognitoPersonaResolver and invokes Bedrock Agent through gagent_client.invoke()."
  role             = aws_iam_role.lambda.arn
  handler          = "handler.handler"
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = merge(
      {
        GAGENT_AGENT_ID                    = var.agent_id
        GAGENT_AGENT_ALIAS_ID              = var.agent_alias_id
        GAGENT_DISPATCHER_ROLE_ARN         = var.persona_role_arns["dispatcher"]
        GAGENT_TECHNICIAN_LEAD_ROLE_ARN    = var.persona_role_arns["technician_lead"]
        GAGENT_OWNER_ROLE_ARN              = var.persona_role_arns["owner"]
        GAGENT_LOG_GROUP                   = var.invocation_log_group
        GAGENT_GATEWAY_PERSONA_RESOLUTION  = var.persona_resolution_mode
        GAGENT_GATEWAY_ALLOWED_ORIGINS     = join(",", var.cors_allowed_origins)
        GAGENT_GOVERNED_QUERY_LAMBDA_NAME  = var.governed_query_lambda_name
        LOG_LEVEL                          = var.log_level
      },
      var.default_service_region != null ? {
        GAGENT_DEFAULT_SERVICE_REGION = var.default_service_region
      } : {},
    )
  }

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda,
  ]
}

resource "aws_lambda_permission" "apigw_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gateway.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
