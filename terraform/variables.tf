variable "aws_region" {
  description = "AWS region to deploy resources"
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name of the project"
  default     = "aws-serverless-ai"
}

variable "bedrock_model_id" {
  description = "The ID of the Bedrock model to use"
  default     = "amazon.nova-lite-v1:0"
}

variable "domain_name" {
  description = "Custom domain name (e.g., example.com) for CloudFront"
  default     = ""
}

variable "certificate_arn" {
  description = "ACM Certificate ARN for the custom domain (must be in us-east-1)"
  default     = ""
}
