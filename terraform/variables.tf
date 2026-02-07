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
