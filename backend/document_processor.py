"""
Document Processor Lambda
=========================
Triggered by S3 ObjectCreated events when a user uploads a file to the
knowledge-vault bucket.  The function:

1. Downloads the object from S3.
2. Extracts plain text (TXT / CSV natively; PDF & DOCX via Amazon Textract).
3. Splits the text into overlapping chunks.
4. Generates a vector embedding for each chunk using Amazon Titan Text
   Embeddings v2 (via Bedrock).
5. Writes each chunk + embedding to the DynamoDB `document-chunks` table so
   the chat Lambda can perform similarity search later.

Environment variables (set by Terraform):
  CHUNKS_TABLE          – DynamoDB table name for document chunks
  KNOWLEDGE_VAULT_BUCKET – S3 bucket name (used for Textract async jobs)
  AWS_REGION            – region (injected automatically by Lambda runtime)
"""

import json
import os
import re
import time
import urllib.parse
import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# AWS clients
# ---------------------------------------------------------------------------
REGION = os.environ.get("AWS_REGION", "us-east-1")

s3_client        = boto3.client("s3",              region_name=REGION)
bedrock_runtime  = boto3.client("bedrock-runtime", region_name=REGION)
textract_client  = boto3.client("textract",        region_name=REGION)
dynamodb         = boto3.resource("dynamodb",       region_name=REGION)

CHUNKS_TABLE           = os.environ.get("CHUNKS_TABLE")
DOCUMENT_STATUS_TABLE  = os.environ.get("DOCUMENT_STATUS_TABLE")
KNOWLEDGE_VAULT_BUCKET = os.environ.get("KNOWLEDGE_VAULT_BUCKET")
EMBEDDING_MODEL_ID     = "amazon.titan-embed-text-v2:0"

# Hard guardrails – must stay in sync with lambda_function.py constants
MAX_FILE_SIZE_MB   = 10
ALLOWED_EXTENSIONS = {
    "pdf", "docx", "txt", "csv", "md",
    "png", "jpg", "jpeg", "tiff",
}

# Chunking parameters
CHUNK_SIZE    = 500   # target characters per chunk
CHUNK_OVERLAP = 100   # overlap between consecutive chunks

# ---------------------------------------------------------------------------
# Document status helpers
# ---------------------------------------------------------------------------

def _update_status(user_id: str, doc_key: str, status: str,
                   chunk_count: int = 0, error: str = ""):
    """
    Write a processing status record to the document_status DynamoDB table.
    Statuses: 'processing' | 'indexed' | 'error'
    Silently no-ops when DOCUMENT_STATUS_TABLE is not configured.
    """
    if not DOCUMENT_STATUS_TABLE:
        return
    table = dynamodb.Table(DOCUMENT_STATUS_TABLE)
    item = {
        "user_id":      user_id,
        "doc_key":      doc_key,
        "filename":     doc_key.split("/")[-1],
        "status":       status,
        "chunk_count":  chunk_count,
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if error:
        item["error"] = error
    table.put_item(Item=item)


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def extract_text_from_txt(body_bytes: bytes) -> str:
    """Decode raw bytes as UTF-8 text (works for .txt and .csv)."""
    return body_bytes.decode("utf-8", errors="replace")


def extract_text_with_textract_sync(bucket: str, key: str) -> str:
    """
    Run Textract DetectDocumentText (synchronous) on a single-page document.
    For multi-page PDFs the async API is more appropriate; this version falls
    back to async automatically when the synchronous call is not available
    (i.e. for PDFs).
    """
    try:
        response = textract_client.detect_document_text(
            Document={"S3Object": {"Bucket": bucket, "Name": key}}
        )
        lines = [
            block["Text"]
            for block in response.get("Blocks", [])
            if block.get("BlockType") == "LINE"
        ]
        return "\n".join(lines)
    except textract_client.exceptions.UnsupportedDocumentException:
        # Multi-page PDF – use async API
        return extract_text_with_textract_async(bucket, key)
    except ClientError as e:
        print(f"Textract sync error: {e}")
        raise


def extract_text_with_textract_async(bucket: str, key: str) -> str:
    """
    Start an async Textract job and poll until completion.
    Suitable for multi-page PDFs.
    """
    response = textract_client.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}}
    )
    job_id = response["JobId"]

    # Poll with exponential back-off (max ~2 minutes total)
    delay = 5
    for _ in range(15):
        time.sleep(delay)
        result = textract_client.get_document_text_detection(JobId=job_id)
        status = result["JobStatus"]
        if status == "SUCCEEDED":
            lines = [
                block["Text"]
                for block in result.get("Blocks", [])
                if block.get("BlockType") == "LINE"
            ]
            # Handle pagination
            next_token = result.get("NextToken")
            while next_token:
                page = textract_client.get_document_text_detection(
                    JobId=job_id, NextToken=next_token
                )
                lines += [
                    block["Text"]
                    for block in page.get("Blocks", [])
                    if block.get("BlockType") == "LINE"
                ]
                next_token = page.get("NextToken")
            return "\n".join(lines)
        elif status == "FAILED":
            raise RuntimeError(f"Textract job {job_id} failed")
        delay = min(delay * 2, 30)

    raise TimeoutError(f"Textract job {job_id} did not complete in time")


def extract_text(bucket: str, key: str) -> str:
    """Dispatch text extraction based on file extension."""
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""

    if ext in ("txt", "csv", "md"):
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        return extract_text_from_txt(obj["Body"].read())

    if ext in ("pdf", "png", "jpg", "jpeg", "tiff"):
        return extract_text_with_textract_sync(bucket, key)

    if ext in ("docx",):
        # Textract supports DOCX natively
        return extract_text_with_textract_sync(bucket, key)

    # Fallback: try to read as plain text
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        return extract_text_from_txt(obj["Body"].read())
    except Exception as e:
        raise ValueError(f"Unsupported file type '.{ext}': {e}")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    """
    Split *text* into overlapping chunks of approximately *chunk_size*
    characters.  Splits prefer sentence / word boundaries.
    Returns a list of (chunk_index, chunk_text) tuples.
    """
    # Normalise whitespace
    text = re.sub(r"\n{3,}", "\n\n", text.strip())

    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            # Try to break at the last sentence boundary before `end`
            boundary = max(
                text.rfind(". ", start, end),
                text.rfind(".\n", start, end),
                text.rfind("\n\n", start, end),
            )
            if boundary != -1 and boundary > start + overlap:
                end = boundary + 1  # include the period
            else:
                # Fall back to last space
                space = text.rfind(" ", start, end)
                if space > start:
                    end = space

        chunk = text[start:end].strip()
        if chunk:
            chunks.append((idx, chunk))
            idx += 1
        start = end - overlap  # slide window with overlap

    return chunks


# ---------------------------------------------------------------------------
# Embedding generation
# ---------------------------------------------------------------------------

def generate_embedding(text: str) -> list[float]:
    """
    Call Amazon Titan Text Embeddings v2 and return the embedding vector.
    The model returns a 1024-dimensional float vector by default.
    """
    body = json.dumps({"inputText": text, "dimensions": 256, "normalize": True})
    response = bedrock_runtime.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(response["body"].read())
    return result["embedding"]


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------

def store_chunk(table, user_id: str, doc_key: str, chunk_idx: int,
                chunk_text_val: str, embedding: list[float]):
    """
    Write one document chunk to DynamoDB.

    Schema:
      PK  user_id     (S)
      SK  chunk_id    (S)  – e.g.  "<doc_key>#<chunk_idx>"
      doc_key         (S)  – full S3 key
      filename        (S)  – basename only
      chunk_index     (N)
      chunk_text      (S)
      embedding       (S)  – JSON-encoded list[float]  (stored as string to
                             avoid DynamoDB Number precision limits)
    """
    chunk_id = f"{doc_key}#{chunk_idx:05d}"
    filename = doc_key.split("/")[-1]

    # Convert floats to Decimal for DynamoDB (only needed for Number types;
    # we store the embedding as a JSON string to avoid conversion overhead)
    table.put_item(
        Item={
            "user_id":     user_id,
            "chunk_id":    chunk_id,
            "doc_key":     doc_key,
            "filename":    filename,
            "chunk_index": chunk_idx,
            "chunk_text":  chunk_text_val,
            "embedding":   json.dumps(embedding),  # serialised as string
        }
    )


def delete_existing_chunks(table, user_id: str, doc_key: str):
    """
    Remove all previously stored chunks for a given document so that
    re-uploaded files don't accumulate stale data.
    """
    # Query all chunks for this (user_id, doc_key) prefix
    response = table.query(
        KeyConditionExpression=(
            boto3.dynamodb.conditions.Key("user_id").eq(user_id)
            & boto3.dynamodb.conditions.Key("chunk_id").begins_with(doc_key)
        )
    )
    items = response.get("Items", [])
    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(
                Key={"user_id": item["user_id"], "chunk_id": item["chunk_id"]}
            )


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """
    Invoked by S3 ObjectCreated notifications.  Each record in *event*
    represents one uploaded file.
    """
    if not CHUNKS_TABLE:
        raise EnvironmentError("CHUNKS_TABLE environment variable is not set")

    table = dynamodb.Table(CHUNKS_TABLE)
    results = []

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # S3 URL-encodes the key (spaces → +, other chars → %XX)
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])

        print(f"Processing s3://{bucket}/{key}")

        # Derive user_id from the S3 key prefix (format: <user_id>/<filename>)
        parts = key.split("/", 1)
        if len(parts) != 2 or not parts[1]:
            print(f"Skipping unexpected key format: {key}")
            continue

        user_id, filename = parts

        try:
            # 0. Mark document as processing
            _update_status(user_id, key, "processing")

            # Guard: check file extension
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in ALLOWED_EXTENSIONS:
                msg = f"Unsupported file type '.{ext}'. Skipping."
                print(msg)
                _update_status(user_id, key, "error", error=msg)
                results.append({"key": key, "status": "error", "error": msg})
                continue

            # Guard: check file size before downloading
            head = s3_client.head_object(Bucket=bucket, Key=key)
            size_bytes = head.get("ContentLength", 0)
            if size_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
                msg = f"File exceeds {MAX_FILE_SIZE_MB} MB limit ({size_bytes / 1024 / 1024:.1f} MB). Skipping."
                print(msg)
                _update_status(user_id, key, "error", error=msg)
                results.append({"key": key, "status": "error", "error": msg})
                continue

            # 1. Extract text
            raw_text = extract_text(bucket, key)
            print(f"Extracted {len(raw_text)} characters from {filename}")

            if not raw_text.strip():
                msg = f"No extractable text found in '{filename}'."
                print(msg)
                _update_status(user_id, key, "error", error=msg)
                results.append({"key": key, "status": "error", "error": msg})
                continue

            # 2. Delete stale chunks from a previous upload of the same file
            delete_existing_chunks(table, user_id, key)

            # 3. Chunk the text
            chunks = chunk_text(raw_text)
            print(f"Split into {len(chunks)} chunks")

            # 4. Generate embeddings and store each chunk
            for chunk_idx, chunk_body in chunks:
                embedding = generate_embedding(chunk_body)
                store_chunk(table, user_id, key, chunk_idx, chunk_body, embedding)

            print(f"Stored {len(chunks)} chunks for {key}")
            _update_status(user_id, key, "indexed", chunk_count=len(chunks))
            results.append({"key": key, "status": "ok", "chunks": len(chunks)})

        except Exception as e:
            print(f"Error processing {key}: {e}")
            _update_status(user_id, key, "error", error=str(e))
            results.append({"key": key, "status": "error", "error": str(e)})

    return {"processed": results}
