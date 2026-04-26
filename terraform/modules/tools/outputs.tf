output "lambda_function_name" {
  description = "Name of the query_ambassadors Lambda."
  value       = aws_lambda_function.query_ambassadors.function_name
}

output "lambda_arn" {
  description = "ARN of the query_ambassadors Lambda."
  value       = aws_lambda_function.query_ambassadors.arn
}

output "lambda_role_arn" {
  description = "Lambda execution role ARN. Persona role trust policies must include this principal."
  value       = aws_iam_role.lambda.arn
}

output "openapi_schema_s3_uri" {
  description = "S3 URI where the OpenAPI 3 schema for the action group is stored."
  value       = "s3://${aws_s3_object.openapi_schema.bucket}/${aws_s3_object.openapi_schema.key}"
}

output "openapi_schema_inline" {
  description = "Inline OpenAPI 3 schema JSON. The agent module accepts either inline or S3-hosted; this output supports both."
  value       = local.openapi_schema
}
