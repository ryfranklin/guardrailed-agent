variable "env" {
  description = "Environment name (e.g., demo). Used in resource naming."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for resource names. Defaults to gagent-."
  type        = string
  default     = "gagent-"
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}

variable "domain_name" {
  description = "Public domain the SPA is served from (e.g., demo.ms3dm.tech). Becomes the CloudFront alternate domain name and the ACM cert's CN."
  type        = string
}

variable "additional_domain_names" {
  description = "Additional alternate domain names on the CloudFront distribution and ACM SAN list. Empty by default. Add to migrate domains without an apply outage."
  type        = list(string)
  default     = []
}

variable "price_class" {
  description = "CloudFront price class. PriceClass_100 = North America + Europe (cheapest); PriceClass_All = global edge."
  type        = string
  default     = "PriceClass_100"
}

variable "minimum_protocol_version" {
  description = "Minimum TLS version on the viewer connection."
  type        = string
  default     = "TLSv1.2_2021"
}

variable "default_ttl_seconds" {
  description = "Default TTL for the CloudFront cache when the origin does not set Cache-Control. Used by the inline cache policy. Set high for the SPA bundle (CI invalidates after each deploy)."
  type        = number
  default     = 86400
}

variable "max_ttl_seconds" {
  description = "Maximum TTL for the CloudFront cache."
  type        = number
  default     = 604800
}

variable "acm_validation_timeout" {
  description = "How long to wait for ACM cert validation. The operator must add the DNS records at IONOS within this window."
  type        = string
  default     = "30m"
}

variable "log_retention_days" {
  description = "CloudWatch retention applied to the CloudFront function's runtime log group (CloudFront emits to /aws/cloudfront/function/<name>)."
  type        = number
  default     = 30
}
