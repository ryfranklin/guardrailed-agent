locals {
  spa_fallback_function_name = "${var.name_prefix}spa-fallback-${var.env}"

  # AWS-managed cache policy "CachingOptimized" — recommended for static
  # assets behind CloudFront. Stable AWS-published ID.
  managed_cache_policy_caching_optimized = "658327ea-f89d-4fab-a63d-7e88639e58f6"
}

# Rewrite client-side route paths to /index.html so the SPA's BrowserRouter
# resolves them. Asset paths and anything containing a '.' (favicon.svg,
# /assets/index-*.js, etc.) pass through untouched. CloudFront's custom
# error responses (403 + 404 -> /index.html with status 200) are a
# belt-and-braces backstop for the same edge cases the function handles.
resource "aws_cloudfront_function" "spa_fallback" {
  name    = local.spa_fallback_function_name
  runtime = "cloudfront-js-2.0"
  comment = "Public web demo SPA fallback — rewrites client-side routes to /index.html."
  publish = true
  code    = <<-EOT
    function handler(event) {
      var request = event.request;
      var uri = request.uri;
      if (uri.indexOf('.') !== -1 || uri.indexOf('/assets/') === 0) {
        return request;
      }
      request.uri = '/index.html';
      return request;
    }
  EOT
}

resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "Public web demo for ${var.domain_name}"
  default_root_object = "index.html"
  price_class         = var.price_class
  http_version        = "http2"
  aliases             = concat([var.domain_name], var.additional_domain_names)

  origin {
    domain_name              = aws_s3_bucket.this.bucket_regional_domain_name
    origin_id                = local.origin_id
    origin_access_control_id = aws_cloudfront_origin_access_control.this.id
  }

  default_cache_behavior {
    target_origin_id       = local.origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = local.managed_cache_policy_caching_optimized

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_fallback.arn
    }
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.this.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = var.minimum_protocol_version
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  tags = var.tags
}
