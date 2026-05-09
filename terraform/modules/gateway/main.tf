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

data "aws_region" "current" {}

locals {
  api_name         = "${var.name_prefix}gateway-${var.env}"
  authorizer_name  = "${var.name_prefix}cognito-${var.env}"
  jwt_issuer_url   = "https://${var.cognito_user_pool_endpoint}"
  access_log_group = "/aws/apigateway/${local.api_name}"
  access_log_format = jsonencode({
    requestId          = "$context.requestId"
    ip                 = "$context.identity.sourceIp"
    requestTime        = "$context.requestTime"
    httpMethod         = "$context.httpMethod"
    routeKey           = "$context.routeKey"
    status             = "$context.status"
    protocol           = "$context.protocol"
    responseLength     = "$context.responseLength"
    integrationStatus  = "$context.integrationStatus"
    integrationLatency = "$context.integrationLatency"
    jwtSub             = "$context.authorizer.claims.sub"
    jwtEmail           = "$context.authorizer.claims.email"
  })
}

resource "aws_apigatewayv2_api" "this" {
  name          = local.api_name
  description   = "Public web demo gateway. Routes POST /ask to the gateway Lambda under a Cognito JWT authorizer (ADR-010)."
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins     = var.cors_allowed_origins
    allow_methods     = ["POST", "OPTIONS"]
    allow_headers     = ["authorization", "content-type", "x-gagent-surface"]
    expose_headers    = []
    allow_credentials = true
    max_age           = 600
  }

  tags = var.tags
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.this.id
  name             = local.authorizer_name
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    audience = [var.cognito_user_pool_client_id]
    issuer   = local.jwt_issuer_url
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.gateway.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "post_ask" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "POST /ask"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "post_preview" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "POST /preview"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_cloudwatch_log_group" "access" {
  name              = local.access_log_group
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format          = local.access_log_format
  }

  default_route_settings {
    detailed_metrics_enabled = true
    throttling_burst_limit   = 50
    throttling_rate_limit    = 25
  }

  tags = var.tags
}
