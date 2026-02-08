import json
import boto3
import os
import datetime
from botocore.exceptions import ClientError

bedrock_runtime = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
s3_client = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
table_name = os.environ.get('USER_USAGE_TABLE')
vault_bucket = os.environ.get('KNOWLEDGE_VAULT_BUCKET')

if table_name:
    usage_table = dynamodb.Table(table_name)
else:
    usage_table = None

DAILY_MSG_LIMIT = 20
MAX_FILES_PER_USER = 5
MAX_FILE_SIZE_MB = 10

def handle_documents(event, user_id, claims):
    http_method = event.get('requestContext', {}).get('http', {}).get('method')
    
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
                'body': json.dumps({'files': files})
            }
        except ClientError as e:
            print(f"Error listing files: {e}")
            return {'statusCode': 500, 'body': json.dumps({'error': 'Failed to list files'})}

    elif http_method == 'POST':
        # Generate Presigned URL for Upload
        # First check count
        prefix = f"{user_id}/"
        try:
            list_resp = s3_client.list_objects_v2(Bucket=vault_bucket, Prefix=prefix)
            current_count = list_resp.get('KeyCount', 0)
            if current_count >= MAX_FILES_PER_USER:
                 return {'statusCode': 400, 'body': json.dumps({'error': f'Max {MAX_FILES_PER_USER} files allowed.'})}
            
            body = json.loads(event.get('body', '{}'))
            filename = body.get('filename')
            file_type = body.get('fileType')
            
            if not filename or '/' in filename:
                return {'statusCode': 400, 'body': json.dumps({'error': 'Invalid filename'})}
            
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
                return {'statusCode': 500, 'body': json.dumps({'error': 'Failed to generate upload URL'})}
            
            return {
                'statusCode': 200,
                'body': json.dumps({'uploadUrl': presigned_url, 'key': key})
            }

        except Exception as e:
            print(f"Error in POST /documents: {e}")
            return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
            
    elif http_method == 'DELETE':
        # Delete file
        # Expect filename in query string or body using standard queryStringParameters 
        # (APIGW v2 uses queryStringParameters directly in event)
        params = event.get('queryStringParameters', {})
        filename = params.get('filename')
        
        if not filename:
             return {'statusCode': 400, 'body': json.dumps({'error': 'Filename required'})}
             
        key = f"{user_id}/{filename}"
        
        try:
            s3_client.delete_object(Bucket=vault_bucket, Key=key)
            return {'statusCode': 200, 'body': json.dumps({'message': 'Deleted'})}
        except ClientError as e:
            return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
            
    return {'statusCode': 405, 'body': 'Method Not Allowed'}

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
        if 'requestContext' in event and 'authorizer' in event['requestContext']:
            jwt = event['requestContext']['authorizer'].get('jwt')
            if jwt:
                claims = jwt.get('claims')
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
        path = event.get('requestContext', {}).get('http', {}).get('path', '')
        # For HTTP API, path usually contains e.g. /documents
        if '/documents' in path:
            return handle_documents(event, user_id, claims if 'claims' in locals() else {})
        
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
                'body': json.dumps({'error': 'No body provided'})
            }
            
        if not user_message:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'No message provided'})
            }

        request_body = json.dumps({
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": user_message}]
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
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'reply': completion})
        }
        
    except Exception as e:
        print("Error:", str(e))
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': str(e)})
        }
