output "api_id" {
  description = "API Gateway HTTP API ID."
  value       = aws_apigatewayv2_api.this.id
}

output "api_endpoint" {
  description = "Default API endpoint, e.g. https://<id>.execute-api.<region>.amazonaws.com. SPA POSTs to <api_endpoint>/ask."
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "api_arn" {
  description = "API Gateway HTTP API ARN."
  value       = aws_apigatewayv2_api.this.arn
}

output "stage_invoke_url" {
  description = "Invoke URL for the $default stage. Same as api_endpoint for $default stage but explicit."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "stage_arn" {
  description = "ARN of the $default stage. Useful for additional WAF / monitoring associations."
  value       = aws_apigatewayv2_stage.default.arn
}

output "lambda_function_name" {
  description = "Gateway Lambda function name."
  value       = aws_lambda_function.gateway.function_name
}

output "lambda_arn" {
  description = "Gateway Lambda ARN. Phase 3.b's Slack adapter invokes this directly via Lambda Invoke."
  value       = aws_lambda_function.gateway.arn
}

output "lambda_role_arn" {
  description = "Lambda execution role ARN. Add to identity.trusted_assumer_arns so the persona role trust policies allow this Lambda to assume them."
  value       = aws_iam_role.lambda.arn
}

output "web_acl_arn" {
  description = "WAF v2 Web ACL ARN attached to the API stage."
  value       = aws_wafv2_web_acl.this.arn
}

output "access_log_group_name" {
  description = "CloudWatch log group receiving API Gateway access logs."
  value       = aws_cloudwatch_log_group.access.name
}

output "lambda_log_group_name" {
  description = "CloudWatch log group receiving the gateway Lambda's runtime logs."
  value       = aws_cloudwatch_log_group.lambda.name
}
