from flask import Flask, request, make_response
import json
import os
import sys
import base64
from dotenv import load_dotenv

# --- Load .env file for local development ---
# This automatically finds the .env file and loads variables into os.environ
load_dotenv()

# Set default env vars BEFORE importing lambda_function
if 'AWS_REGION' not in os.environ:
    os.environ['AWS_REGION'] = 'us-east-1'

if 'KNOWLEDGE_VAULT_BUCKET' not in os.environ:
    print("WARNING: KNOWLEDGE_VAULT_BUCKET not set. Listing/Uploading files will fail (500) if connecting to real AWS.")
    os.environ['KNOWLEDGE_VAULT_BUCKET'] = 'local-vault-bucket'

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from lambda_function import lambda_handler

app = Flask(__name__)


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

@app.route('/<path:text>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
@app.route('/', defaults={'text': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
def proxy(text):
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        return response

    # Mock API Gateway event structure (HTTP API payload 2.0 ish)
    path = '/' + text
    
    event = {
        'rawPath': path,
        'requestContext': {
            'http': {
                'method': request.method,
                'path': path
            },
            'authorizer': {'jwt': {'claims': {}}}
        },
        'queryStringParameters': request.args.to_dict(),
        'body': request.data.decode('utf-8') if request.data else '{}'
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

    # Ensure CORS headers are present on all responses (fix for local dev)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        
    return resp

if __name__ == '__main__':
    print("Starting local backend server...")
    print("Ensure you have AWS credentials configured (e.g. via 'aws configure' or env vars)")
    app.run(port=8000, debug=True)
