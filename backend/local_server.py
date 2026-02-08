from flask import Flask, request, make_response
from lambda_function import lambda_handler
import json
import os
import sys
import base64

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)

# Set default env vars for local testing to match Terraform defaults
if 'AWS_REGION' not in os.environ:
    os.environ['AWS_REGION'] = 'us-east-1'

# Helper to decode JWT payload without verification (for local simulation only)
def decode_jwt_payload(token):
    try:
        # JWT is header.payload.signature
        parts = token.split('.')
        if len(parts) < 2:
            return {}
        payload = parts[1]
        # Base64 decode needs padding
        payload += '=' * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        print(f"Error decoding JWT: {e}")
        return {}

@app.route('/', methods=['POST', 'OPTIONS'])
def index():
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "OPTIONS,POST")
        return response

    # Mock API Gateway event structure
    event = {
        'body': request.data.decode('utf-8'),
        'requestContext': {
            'authorizer': {'jwt': {'claims': {}}}
        }
    }
    
    # Extract Authorization header
    auth_header = request.headers.get('Authorization')
    if auth_header:
        # If the header mimics "Bearer <token>", strip "Bearer "
        if auth_header.startswith("Bearer "):
            auth_header = auth_header[7:]
            
        claims = decode_jwt_payload(auth_header)
        event['requestContext']['authorizer']['jwt']['claims'] = claims

    # Call the actual lambda function
    result = lambda_handler(event, None)
    
    # Parse the response from Lambda
    response_body = result.get('body', '{}')
    status_code = result.get('statusCode', 200)
    headers = result.get('headers', {})

    # Create Flask response
    resp = make_response(response_body, status_code)
    
    # Forward headers returned by Lambda
    for key, value in headers.items():
        resp.headers[key] = value
        
    return resp

if __name__ == '__main__':
    print("Starting local backend server...")
    print("Ensure you have AWS credentials configured (e.g. via 'aws configure' or env vars)")
    app.run(port=8000, debug=True)
