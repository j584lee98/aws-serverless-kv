import json
import math
import boto3
import os
import datetime
from botocore.exceptions import ClientError

bedrock_runtime = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
s3_client = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
table_name = os.environ.get('USER_USAGE_TABLE')
vault_bucket = os.environ.get('KNOWLEDGE_VAULT_BUCKET')
chunks_table_name = os.environ.get('CHUNKS_TABLE')
doc_status_table_name = os.environ.get('DOCUMENT_STATUS_TABLE')

# RAG settings
EMBEDDING_MODEL_ID = 'amazon.titan-embed-text-v2:0'
RAG_TOP_K          = 5      # number of chunks to inject into the prompt
RAG_MAX_CHARS      = 4000   # safety cap on total context characters

if table_name:
    usage_table = dynamodb.Table(table_name)
else:
    usage_table = None

if chunks_table_name:
    chunks_table = dynamodb.Table(chunks_table_name)
else:
    chunks_table = None

if doc_status_table_name:
    doc_status_table = dynamodb.Table(doc_status_table_name)
else:
    doc_status_table = None

# ---------------------------------------------------------------------------
# RAG helpers
# ---------------------------------------------------------------------------

def _embed_text(text: str) -> list:
    """
    Generate a 256-dim vector for *text* using Titan Text Embeddings v2.
    Returns an empty list if embedding is unavailable (graceful degradation).
    """
    try:
        body = json.dumps({'inputText': text, 'dimensions': 256, 'normalize': True})
        resp = bedrock_runtime.invoke_model(
            modelId=EMBEDDING_MODEL_ID,
            body=body,
            contentType='application/json',
            accept='application/json',
        )
        return json.loads(resp['body'].read())['embedding']
    except Exception as e:
        print(f'Embedding error: {e}')
        return []


def _cosine_similarity(a: list, b: list) -> float:
    """Cosine similarity between two equal-length vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def retrieve_context(user_id: str, query: str, top_k: int = RAG_TOP_K) -> list:
    """
    Embed *query*, fetch all chunks for *user_id* from DynamoDB, rank by
    cosine similarity, and return the top-k chunk dicts above the score
    threshold.

    Each returned dict has keys: chunk_text, filename, chunk_index, score.
    Returns an empty list when the chunks table is unavailable or when the
    user has no documents indexed.
    """
    if not chunks_table:
        return []

    query_embedding = _embed_text(query)
    if not query_embedding:
        return []

    # Load all chunks for this user (pagination-aware)
    chunks = []
    last_key = None
    while True:
        kwargs = {
            'KeyConditionExpression': (
                boto3.dynamodb.conditions.Key('user_id').eq(user_id)
            )
        }
        if last_key:
            kwargs['ExclusiveStartKey'] = last_key
        response = chunks_table.query(**kwargs)
        chunks.extend(response.get('Items', []))
        last_key = response.get('LastEvaluatedKey')
        if not last_key:
            break

    if not chunks:
        return []

    # Score every chunk; apply relevance threshold
    scored = []
    for item in chunks:
        try:
            stored_emb = json.loads(item.get('embedding', '[]'))
        except (json.JSONDecodeError, TypeError):
            continue
        score = _cosine_similarity(query_embedding, stored_emb)
        if score < RAG_SCORE_THRESHOLD:
            continue
        scored.append({
            'chunk_text':  item.get('chunk_text', ''),
            'filename':    item.get('filename', ''),
            'chunk_index': int(item.get('chunk_index', 0)),
            'score':       score,
        })

    # Return top-k by descending score
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:top_k]


def build_rag_prompt(user_message: str, context_chunks: list) -> str:
    """
    Wrap the user message with retrieved context chunks.
    Falls back to the bare user message when no context is available.
    """
    if not context_chunks:
        return user_message

    context_parts = []
    total_chars   = 0
    for chunk in context_chunks:
        text = chunk['chunk_text'].strip()
        if total_chars + len(text) > RAG_MAX_CHARS:
            break
        context_parts.append(
            f"[Source: {chunk['filename']}, chunk {chunk['chunk_index']}]\n{text}"
        )
        total_chars += len(text)

    context_block = '\n\n---\n\n'.join(context_parts)
    return (
        f"Use the following excerpts from the user's documents to help answer "
        f"their question. If the excerpts are not relevant, answer from your "
        f"general knowledge instead.\n\n"
        f"CONTEXT:\n{context_block}\n\n"
        f"USER QUESTION:\n{user_message}"
    )


DAILY_MSG_LIMIT    = 20
MAX_FILES_PER_USER = 5
MAX_FILE_SIZE_MB   = 10
MAX_QUERY_LENGTH   = 2000   # chars; hard cap on user message
RAG_SCORE_THRESHOLD = 0.30  # ignore chunks below this cosine similarity

# File types accepted at upload time.
# Must match what document_processor.py's extract_text() can handle.
ALLOWED_EXTENSIONS = {
    'pdf', 'docx', 'txt', 'csv', 'md',
    'png', 'jpg', 'jpeg', 'tiff',
}

def handle_documents(event, user_id, claims, headers):
    http_method = event.get('requestContext', {}).get('http', {}).get('method')
    if not http_method:
        http_method = event.get('httpMethod')
    
    if http_method == 'GET':
        # List files, enriched with indexing status
        prefix = f"{user_id}/"
        try:
            response = s3_client.list_objects_v2(Bucket=vault_bucket, Prefix=prefix)
            files = []
            if 'Contents' in response:
                for obj in response['Contents']:
                    filename = obj['Key'].split('/')[-1]
                    file_entry = {
                        'name': filename,
                        'size': obj['Size'],
                        'lastModified': str(obj['LastModified']),
                        'indexStatus': 'unknown',
                    }
                    # Enrich with processing status from DynamoDB
                    if doc_status_table:
                        try:
                            status_resp = doc_status_table.get_item(
                                Key={'user_id': user_id, 'doc_key': obj['Key']}
                            )
                            status_item = status_resp.get('Item')
                            if status_item:
                                file_entry['indexStatus']   = status_item.get('status', 'unknown')
                                file_entry['chunkCount']    = int(status_item.get('chunk_count', 0))
                                file_entry['lastIndexed']   = status_item.get('last_updated', '')
                                if status_item.get('error'):
                                    file_entry['indexError'] = status_item['error']
                        except Exception as status_err:
                            print(f"Status lookup error for {obj['Key']}: {status_err}")
                    files.append(file_entry)
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps({'files': files})
            }
        except ClientError as e:
            print(f"Error listing files: {e}")
            return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': 'Failed to list files'})}

    elif http_method == 'POST':
        # Generate Presigned URL for Upload
        body = json.loads(event.get('body', '{}'))
        filename  = body.get('filename', '')
        file_type = body.get('fileType', '')
        file_size = body.get('fileSize', 0)  # bytes, supplied by client

        # --- Input validation ---
        if not filename or '/' in filename or '..' in filename:
            return {'statusCode': 400, 'headers': headers, 'body': json.dumps({'error': 'Invalid filename'})}

        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext not in ALLOWED_EXTENSIONS:
            allowed_str = ', '.join(sorted(ALLOWED_EXTENSIONS))
            return {
                'statusCode': 400, 'headers': headers,
                'body': json.dumps({'error': f'File type .{ext} is not supported. Allowed: {allowed_str}'})
            }

        if file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            return {
                'statusCode': 400, 'headers': headers,
                'body': json.dumps({'error': f'File exceeds {MAX_FILE_SIZE_MB} MB size limit.'})
            }

        # First check count
        prefix = f"{user_id}/"
        try:
            list_resp = s3_client.list_objects_v2(Bucket=vault_bucket, Prefix=prefix)
            current_count = list_resp.get('KeyCount', 0)
            if current_count >= MAX_FILES_PER_USER:
                 return {'statusCode': 400, 'headers': headers, 'body': json.dumps({'error': f'Maximum of {MAX_FILES_PER_USER} files allowed. Delete a file to upload a new one.'})}
            
            key = f"{user_id}/{filename}"
            
            # Generate presigned URL
            try:
                presigned_url = s3_client.generate_presigned_url(
                    'put_object',
                    Params={
                        'Bucket': vault_bucket,
                        'Key': key,
                        'ContentType': file_type
                    },
                    ExpiresIn=300
                )
            except Exception as e:
                print(f"Error generating presigned URL for bucket {vault_bucket}: {e}")
                return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': 'Failed to generate upload URL'})}
            
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps({'uploadUrl': presigned_url, 'key': key})
            }

        except Exception as e:
            print(f"Error in POST /documents: {e}")
            return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': str(e)})}
            
    elif http_method == 'DELETE':
        # Delete file
        # Expect filename in query string or body using standard queryStringParameters 
        # (APIGW v2 uses queryStringParameters directly in event)
        params = event.get('queryStringParameters', {})
        filename = params.get('filename')
        
        if not filename:
             return {'statusCode': 400, 'headers': headers, 'body': json.dumps({'error': 'Filename required'})}
             
        key = f"{user_id}/{filename}"
        
        try:
            # Remove the file from S3
            s3_client.delete_object(Bucket=vault_bucket, Key=key)

            # Purge all DynamoDB chunks associated with this file
            if chunks_table:
                try:
                    resp = chunks_table.query(
                        KeyConditionExpression=(
                            boto3.dynamodb.conditions.Key('user_id').eq(user_id)
                            & boto3.dynamodb.conditions.Key('chunk_id').begins_with(key)
                        )
                    )
                    chunk_items = resp.get('Items', [])
                    if chunk_items:
                        with chunks_table.batch_writer() as batch:
                            for item in chunk_items:
                                batch.delete_item(
                                    Key={'user_id': item['user_id'], 'chunk_id': item['chunk_id']}
                                )
                        print(f"Purged {len(chunk_items)} chunks for {key}")
                except Exception as chunk_err:
                    print(f"Chunk purge error (non-fatal): {chunk_err}")

            # Remove the status record
            if doc_status_table:
                try:
                    doc_status_table.delete_item(
                        Key={'user_id': user_id, 'doc_key': key}
                    )
                except Exception as status_err:
                    print(f"Status delete error (non-fatal): {status_err}")

            return {'statusCode': 200, 'headers': headers, 'body': json.dumps({'message': 'Deleted'})}
        except ClientError as e:
            return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': str(e)})}
            
    return {'statusCode': 405, 'headers': headers, 'body': 'Method Not Allowed'}

def check_and_update_quota(user_id):
    if not usage_table:
        return True # specific usage logic skipped if no table (e.g. local dev without ddb local)
        
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    
    try:
        response = usage_table.update_item(
            Key={
                'user_id': user_id,
                'date': today
            },
            UpdateExpression="SET request_count = if_not_exists(request_count, :start) + :inc",
            ExpressionAttributeValues={
                ':start': 0,
                ':inc': 1,
                ':limit': DAILY_MSG_LIMIT
            },
            ConditionExpression="request_count < :limit OR attribute_not_exists(request_count)",
            ReturnValues="UPDATED_NEW"
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        else:
            print("DynamoDB error:", str(e))
            raise e

def lambda_handler(event, context):
    print("Event:", json.dumps(event))
    
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }
        
    try:
        # Auth Check
        user_id = "anonymous"
        user_groups = []
        
        # API Gateway HTTP API with JWT Authorizer puts claims in requestContext
        # Handle both v2.0 (inside 'jwt') and v1.0 (direct in 'authorizer') payload formats
        claims = {}
        if 'requestContext' in event and 'authorizer' in event['requestContext']:
            auth_context = event['requestContext']['authorizer']
            
            # Try v2.0 structure first
            jwt = auth_context.get('jwt')
            if jwt and 'claims' in jwt:
                claims = jwt.get('claims') or {}
            # Fallback to v1.0 structure (or direct claims)
            elif 'claims' in auth_context:
                claims = auth_context.get('claims') or {}
            
            if claims:
                user_id = claims.get('sub') # standard cognito user id
                
                # 'cognito:groups' can be a list or a string depending on number of groups
                groups_claim = claims.get('cognito:groups', [])
                if isinstance(groups_claim, list):
                    user_groups = groups_claim
                elif isinstance(groups_claim, str):
                    user_groups = [groups_claim]

        # Enforce Quota
        is_admin = 'Admins' in user_groups

        # --- Check for document routes ---
        # 1. Try to get path from API Gateway V2 structure
        path = event.get('requestContext', {}).get('http', {}).get('path', '')
        raw_path = event.get('rawPath', '')
        
        # 2. Fallback for API Gateway V1 structure
        if not path and not raw_path:
            path = event.get('path', '') 
        
        # For HTTP API, path usually contains e.g. /documents
        if '/documents' in path or '/documents' in raw_path:
            return handle_documents(event, user_id, claims, headers)
        
        # Block unauthenticated callers from the chat endpoint
        if user_id == 'anonymous':
            return {
                'statusCode': 401,
                'headers': headers,
                'body': json.dumps({'error': 'Authentication required'})
            }

        if user_id != "anonymous" and not is_admin:
            allowed = check_and_update_quota(user_id)
            if not allowed:
                 return {
                    'statusCode': 429,
                    'headers': headers,
                    'body': json.dumps({'error': 'Daily message quota exceeded. Limit resets at midnight UTC.'})
                }

        if event.get('body'):
            body = json.loads(event['body'])
            user_message = body.get('message', '')
        else:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'Request body is required'})
            }
            
        if not user_message:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'No message provided'})
            }

        if len(user_message) > MAX_QUERY_LENGTH:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': f'Message too long. Maximum {MAX_QUERY_LENGTH} characters allowed.'})
            }

        # --- RAG: retrieve relevant context from the user's documents ---
        context_chunks = []
        if user_id != 'anonymous':
            try:
                context_chunks = retrieve_context(user_id, user_message)
                print(f"RAG: retrieved {len(context_chunks)} chunks for user {user_id}")
            except Exception as rag_err:
                # RAG is best-effort; don't block the chat if it fails
                print(f"RAG retrieval error (non-fatal): {rag_err}")

        augmented_message = build_rag_prompt(user_message, context_chunks)

        request_body = json.dumps({
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": augmented_message}]
                }
            ],
            "inferenceConfig": {
                "maxTokens": 512,
                "temperature": 0.5,
                "topP": 0.9
            }
        })
        
        model_id = os.environ.get('BEDROCK_MODEL_ID', 'amazon.nova-lite-v1:0')
        
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=request_body
        )
        
        response_body = json.loads(response.get('body').read())
        completion = response_body.get('output').get('message').get('content')[0].get('text')

        # Build the response – include source references when RAG was used
        reply_payload = {'reply': completion}
        if context_chunks:
            reply_payload['sources'] = [
                {
                    'filename':    c['filename'],
                    'chunk_index': c['chunk_index'],
                    'score':       round(c['score'], 4),
                }
                for c in context_chunks
            ]
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps(reply_payload)
        }
        
    except Exception as e:
        print("Error:", str(e))
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': str(e)})
        }
