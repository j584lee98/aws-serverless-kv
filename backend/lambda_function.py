import json
import boto3
import os

bedrock_runtime = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))

def lambda_handler(event, context):
    print("Event:", json.dumps(event))
    
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'OPTIONS,POST'
    }
        
    try:
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
