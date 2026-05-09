output "user_pool_id" {
  description = "Cognito user pool ID. Feed to API Gateway JWT authorizer and the SPA's Amplify config."
  value       = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  description = "Cognito user pool ARN."
  value       = aws_cognito_user_pool.this.arn
}

output "user_pool_endpoint" {
  description = "Cognito user pool endpoint (no scheme), e.g. cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXX."
  value       = aws_cognito_user_pool.this.endpoint
}

output "user_pool_client_id" {
  description = "Cognito user pool client ID for the SPA."
  value       = aws_cognito_user_pool_client.web.id
}

output "hosted_ui_domain" {
  description = "Cognito-managed Hosted UI fully qualified domain (e.g. gagent-demo.auth.us-east-1.amazoncognito.com)."
  value       = "${aws_cognito_user_pool_domain.this.domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
}

output "hosted_ui_domain_prefix" {
  description = "The Cognito Hosted UI domain prefix only (without the .auth.<region>.amazoncognito.com suffix)."
  value       = aws_cognito_user_pool_domain.this.domain
}

output "jwt_issuer_url" {
  description = "JWT issuer URL for the API Gateway JWT authorizer."
  value       = "https://${aws_cognito_user_pool.this.endpoint}"
}

output "supported_identity_providers" {
  description = "List of identity provider names enabled on the user pool client."
  value       = aws_cognito_user_pool_client.web.supported_identity_providers
}
