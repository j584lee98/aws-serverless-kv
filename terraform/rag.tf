# =============================================================================
# RAG Infrastructure – Phase 1: Document Processing Pipeline
# =============================================================================
# Resources added here:
#   1. DynamoDB table  – stores document chunks + embeddings
#   2. IAM role/policies for the document-processor Lambda
#   3. document_processor Lambda function
#   4. S3 event notification – fires the processor on every object upload
#   5. Updated IAM policy on the main chat Lambda adding Bedrock embedding
#      permissions (so it can embed queries at search time)
# =============================================================================

# -----------------------------------------------------------------------------
# 1. DynamoDB – Document Chunks Table
# -----------------------------------------------------------------------------
resource "aws_dynamodb_table" "document_chunks" {
  name         = "${var.project_name}-document-chunks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "chunk_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "chunk_id"
    type = "S"
  }

  # TTL can be enabled later if you want auto-expiry of old chunks
  # ttl { attribute_name = "expires_at" enabled = true }

  tags = {
    Project = var.project_name
    Purpose = "RAG document chunks and embeddings"
  }
}

# -----------------------------------------------------------------------------
# 2. IAM Role for Document Processor Lambda
# -----------------------------------------------------------------------------
resource "aws_iam_role" "doc_processor_role" {
  name = "${var.project_name}-doc-processor-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "doc_processor_basic" {
  role       = aws_iam_role.doc_processor_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# S3 read access (download the uploaded file; Textract also reads from S3)
resource "aws_iam_role_policy" "doc_processor_s3" {
  name = "doc_processor_s3"
  role = aws_iam_role.doc_processor_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:HeadObject"]
        Resource = "${aws_s3_bucket.knowledge_vault.arn}/*"
      }
    ]
  })
}

# Bedrock – embedding model
resource "aws_iam_role_policy" "doc_processor_bedrock" {
  name = "doc_processor_bedrock"
  role = aws_iam_role.doc_processor_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
    }]
  })
}

# Textract – text extraction from PDFs and images
resource "aws_iam_role_policy" "doc_processor_textract" {
  name = "doc_processor_textract"
  role = aws_iam_role.doc_processor_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "textract:DetectDocumentText",
        "textract:StartDocumentTextDetection",
        "textract:GetDocumentTextDetection"
      ]
      Resource = "*"
    }]
  })
}

# DynamoDB – write chunks and update processing status
resource "aws_iam_role_policy" "doc_processor_dynamodb" {
  name = "doc_processor_dynamodb"
  role = aws_iam_role.doc_processor_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:BatchWriteItem"
        ]
        Resource = aws_dynamodb_table.document_chunks.arn
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem"
        ]
        Resource = aws_dynamodb_table.document_status.arn
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# 3. Document Processor Lambda
# -----------------------------------------------------------------------------
data "archive_file" "doc_processor_zip" {
  type        = "zip"
  source_file = "${path.module}/../backend/document_processor.py"
  output_path = "${path.module}/document_processor.zip"
}

resource "aws_lambda_function" "document_processor" {
  filename         = data.archive_file.doc_processor_zip.output_path
  function_name    = "${var.project_name}-document-processor"
  role             = aws_iam_role.doc_processor_role.arn
  handler          = "document_processor.lambda_handler"
  source_code_hash = data.archive_file.doc_processor_zip.output_base64sha256
  runtime          = "python3.12"

  # Text extraction + embedding can take a while for large docs
  timeout     = 300
  memory_size = 512

  environment {
    variables = {
      CHUNKS_TABLE           = aws_dynamodb_table.document_chunks.name
      DOCUMENT_STATUS_TABLE  = aws_dynamodb_table.document_status.name
      KNOWLEDGE_VAULT_BUCKET = aws_s3_bucket.knowledge_vault.bucket
      AWS_REGION_NAME        = var.aws_region
    }
  }

  tags = {
    Project = var.project_name
    Purpose = "RAG document processing"
  }
}

# Allow S3 to invoke the processor Lambda
resource "aws_lambda_permission" "s3_invoke_processor" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.document_processor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.knowledge_vault.arn
}

# -----------------------------------------------------------------------------
# 4. S3 Event Notification → Document Processor Lambda
# -----------------------------------------------------------------------------
resource "aws_s3_bucket_notification" "vault_upload_trigger" {
  bucket = aws_s3_bucket.knowledge_vault.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.document_processor.arn
    events              = ["s3:ObjectCreated:*"]
  }

  # The permission resource must exist before Terraform can attach the
  # notification, so we declare an explicit dependency.
  depends_on = [aws_lambda_permission.s3_invoke_processor]
}

# -----------------------------------------------------------------------------
# 5. Extra IAM – let the main chat Lambda call the embedding model
#    (needed in Phase 2 to embed user queries at search time)
# -----------------------------------------------------------------------------
resource "aws_iam_role_policy" "chat_lambda_embedding" {
  name = "chat_lambda_embedding"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
      },
      {
        # RAG search: read all chunks for a user
        Effect = "Allow"
        Action = ["dynamodb:Query"]
        Resource = aws_dynamodb_table.document_chunks.arn
      },
      {
        # Read processing status for GET /documents + purge on DELETE
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:DeleteItem",
          "dynamodb:BatchWriteItem"
        ]
        Resource = [
          aws_dynamodb_table.document_status.arn,
          aws_dynamodb_table.document_chunks.arn
        ]
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Document Status Table
# -----------------------------------------------------------------------------
resource "aws_dynamodb_table" "document_status" {
  name         = "${var.project_name}-document-status"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "doc_key"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "doc_key"
    type = "S"
  }

  tags = {
    Project = var.project_name
    Purpose = "RAG document processing status"
  }
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------
output "document_chunks_table" {
  description = "DynamoDB table storing RAG document chunks"
  value       = aws_dynamodb_table.document_chunks.name
}

output "document_status_table" {
  description = "DynamoDB table tracking per-document indexing status"
  value       = aws_dynamodb_table.document_status.name
}

output "document_processor_function" {
  description = "Lambda function that processes uploaded documents"
  value       = aws_lambda_function.document_processor.function_name
}
