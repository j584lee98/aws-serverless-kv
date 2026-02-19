import json
import os
import datetime
from botocore.exceptions import ClientError

from config import bedrock_runtime, usage_table
from rag import retrieve_context, build_rag_prompt
from documents import handle_documents

DAILY_MSG_LIMIT  = 20
MAX_QUERY_LENGTH = 2000   # chars; hard cap on user message


def check_and_update_quota(user_id):
    if not usage_table:
        return True  # quota skipped in local dev without DDB

    today = datetime.datetime.now().strftime('%Y-%m-%d')

    try:
        usage_table.update_item(
            Key={'user_id': user_id, 'date': today},
            UpdateExpression="SET request_count = if_not_exists(request_count, :start) + :inc",
            ExpressionAttributeValues={
                ':start': 0,
                ':inc':   1,
                ':limit': DAILY_MSG_LIMIT,
            },
            ConditionExpression="request_count < :limit OR attribute_not_exists(request_count)",
            ReturnValues="UPDATED_NEW",
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        print("DynamoDB error:", str(e))
        raise


def lambda_handler(event, context):
    print("Event:", json.dumps(event))

    headers = {
        'Access-Control-Allow-Origin':  '*',
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
    }

    try:
        # --- Auth ---
        user_id     = "anonymous"
        user_groups = []
        claims      = {}

        if 'requestContext' in event and 'authorizer' in event['requestContext']:
            auth_context = event['requestContext']['authorizer']
            jwt = auth_context.get('jwt')
            if jwt and 'claims' in jwt:
                claims = jwt.get('claims') or {}
            elif 'claims' in auth_context:
                claims = auth_context.get('claims') or {}

            if claims:
                user_id = claims.get('sub')
                groups_claim = claims.get('cognito:groups', [])
                if isinstance(groups_claim, list):
                    user_groups = groups_claim
                elif isinstance(groups_claim, str):
                    user_groups = [groups_claim]

        is_admin = 'Admins' in user_groups

        # --- Route: /documents ---
        path     = event.get('requestContext', {}).get('http', {}).get('path', '')
        raw_path = event.get('rawPath', '')
        if not path and not raw_path:
            path = event.get('path', '')

        if '/documents' in path or '/documents' in raw_path:
            return handle_documents(event, user_id, claims, headers)

        # --- Chat endpoint ---
        if user_id == 'anonymous':
            return {
                'statusCode': 401,
                'headers': headers,
                'body': json.dumps({'error': 'Authentication required'}),
            }

        if not is_admin:
            if not check_and_update_quota(user_id):
                return {
                    'statusCode': 429,
                    'headers': headers,
                    'body': json.dumps({'error': 'Daily message quota exceeded. Limit resets at midnight UTC.'}),
                }

        if not event.get('body'):
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'Request body is required'}),
            }

        body         = json.loads(event['body'])
        user_message = body.get('message', '')

        if not user_message:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'No message provided'}),
            }

        if len(user_message) > MAX_QUERY_LENGTH:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': f'Message too long. Maximum {MAX_QUERY_LENGTH} characters allowed.'}),
            }

        # --- RAG ---
        context_chunks = []
        try:
            context_chunks = retrieve_context(user_id, user_message)
            print(f"RAG: retrieved {len(context_chunks)} chunks for user {user_id}")
        except Exception as rag_err:
            print(f"RAG retrieval error (non-fatal): {rag_err}")

        augmented_message = build_rag_prompt(user_message, context_chunks)

        # --- Bedrock invocation ---
        model_id = os.environ.get('BEDROCK_MODEL_ID', 'amazon.nova-lite-v1:0')
        request_body = json.dumps({
            "system": [{
                "text": (
                    "You are a helpful AI assistant. "
                    "Always respond using Markdown formatting. "
                    "Use headers, bullet points, bold/italic text, code blocks, "
                    "and tables where appropriate to make your responses clear and well-structured."
                )
            }],
            "messages": [{"role": "user", "content": [{"text": augmented_message}]}],
            "inferenceConfig": {"maxTokens": 512, "temperature": 0.5, "topP": 0.9},
        })

        response      = bedrock_runtime.invoke_model(modelId=model_id, body=request_body)
        response_body = json.loads(response['body'].read())
        completion    = response_body['output']['message']['content'][0]['text']

        reply_payload = {'reply': completion}
        if context_chunks:
            reply_payload['sources'] = [
                {'filename': c['filename'], 'chunk_index': c['chunk_index'], 'score': round(c['score'], 4)}
                for c in context_chunks
            ]

        return {'statusCode': 200, 'headers': headers, 'body': json.dumps(reply_payload)}

    except Exception as e:
        print("Error:", str(e))
        return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': str(e)})}
