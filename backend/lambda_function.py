import json
import boto3
import os
import datetime
from botocore.exceptions import ClientError

bedrock_runtime = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
table_name = os.environ.get('USER_USAGE_TABLE')
if table_name:
    usage_table = dynamodb.Table(table_name)
else:
    usage_table = None

DAILY_MSG_LIMIT = 20

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
        'Access-Control-Allow-Methods': 'OPTIONS,POST'
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
