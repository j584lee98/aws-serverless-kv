output "api_url" {
  value = aws_apigatewayv2_api.api.api_endpoint
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.frontend_distribution.id
}

output "s3_bucket_name" {
  value = aws_s3_bucket.frontend_bucket.bucket
}

output "cloudfront_domain_name" {
  value = aws_cloudfront_distribution.frontend_distribution.domain_name
}
