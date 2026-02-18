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
    cosine similarity, and return the top-k chunk dicts.

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

    # Score every chunk
    scored = []
    for item in chunks:
        try:
            stored_emb = json.loads(item.get('embedding', '[]'))
        except (json.JSONDecodeError, TypeError):
            continue
        score = _cosine_similarity(query_embedding, stored_emb)
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


DAILY_MSG_LIMIT = 20
MAX_FILES_PER_USER = 5
MAX_FILE_SIZE_MB = 10

def handle_documents(event, user_id, claims, headers):
    http_method = event.get('requestContext', {}).get('http', {}).get('method')
    if not http_method:
        http_method = event.get('httpMethod')
    
    if http_method == 'GET':
        # List files
        prefix = f"{user_id}/"
        try:
            response = s3_client.list_objects_v2(Bucket=vault_bucket, Prefix=prefix)
            files = []
            if 'Contents' in response:
                for obj in response['Contents']:
                    files.append({
                        'name': obj['Key'].split('/')[-1],
                        'size': obj['Size'],
                        'lastModified': str(obj['LastModified'])
                    })
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
        # First check count
        prefix = f"{user_id}/"
        try:
            list_resp = s3_client.list_objects_v2(Bucket=vault_bucket, Prefix=prefix)
            current_count = list_resp.get('KeyCount', 0)
            if current_count >= MAX_FILES_PER_USER:
                 return {'statusCode': 400, 'headers': headers, 'body': json.dumps({'error': f'Max {MAX_FILES_PER_USER} files allowed.'})}
            
            body = json.loads(event.get('body', '{}'))
            filename = body.get('filename')
            file_type = body.get('fileType')
            
            if not filename or '/' in filename:
                return {'statusCode': 400, 'headers': headers, 'body': json.dumps({'error': 'Invalid filename'})}
            
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
            s3_client.delete_object(Bucket=vault_bucket, Key=key)
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
        if 'requestContext' in event and 'authorizer' in event['requestContext']:
            auth_context = event['requestContext']['authorizer']
            
            # Try v2.0 structure first
            jwt = auth_context.get('jwt')
            if jwt and 'claims' in jwt:
                claims = jwt.get('claims')
            # Fallback to v1.0 structure (or direct claims)
            elif 'claims' in auth_context:
                claims = auth_context.get('claims')
            
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
        
        print(f"DEBUG: path={path}, rawPath={raw_path}")

        # For HTTP API, path usually contains e.g. /documents
        if '/documents' in path or '/documents' in raw_path:
            return handle_documents(event, user_id, claims if 'claims' in locals() else {}, headers)
        
        if user_id != "anonymous" and not is_admin:
            allowed = check_and_update_quota(user_id)
            if not allowed:
                 return {
                    'statusCode': 429,
                    'headers': headers,
                    'body': json.dumps({'error': 'Daily message quota exceeded.'})
                }

        if event.get('body'):
            body = json.loads(event['body'])
            user_message = body.get('message', '')
        else:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'No body provided for Chatbot request (Fallthrough)'})
            }
            
        if not user_message:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'No message provided'})
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
                "max_new_tokens": 512,
                "temperature": 0.5,
                "top_p": 0.9
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
