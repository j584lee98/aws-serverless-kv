"""
Shared AWS clients and DynamoDB table references.
Imported by rag.py, documents.py, and lambda_function.py.
"""
import boto3
import os

REGION = os.environ.get('AWS_REGION', 'us-east-1')

bedrock_runtime = boto3.client('bedrock-runtime', region_name=REGION)
s3_client       = boto3.client('s3',              region_name=REGION)
dynamodb        = boto3.resource('dynamodb',       region_name=REGION)

vault_bucket          = os.environ.get('KNOWLEDGE_VAULT_BUCKET')
table_name            = os.environ.get('USER_USAGE_TABLE')
chunks_table_name     = os.environ.get('CHUNKS_TABLE')
doc_status_table_name = os.environ.get('DOCUMENT_STATUS_TABLE')

usage_table      = dynamodb.Table(table_name)            if table_name            else None
chunks_table     = dynamodb.Table(chunks_table_name)     if chunks_table_name     else None
doc_status_table = dynamodb.Table(doc_status_table_name) if doc_status_table_name else None
