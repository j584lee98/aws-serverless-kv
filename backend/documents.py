"""
Document management handler (upload, list, delete).
Handles the /documents route for the Lambda function.
"""
import json
import boto3.dynamodb.conditions
from botocore.exceptions import ClientError

from config import s3_client, vault_bucket, chunks_table, doc_status_table

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_FILES_PER_USER = 5
MAX_FILE_SIZE_MB   = 10

ALLOWED_EXTENSIONS = {
    'pdf', 'docx', 'txt', 'csv', 'md',
    'png', 'jpg', 'jpeg', 'tiff',
}


def handle_documents(event, user_id, claims, headers):
    http_method = event.get('requestContext', {}).get('http', {}).get('method')
    if not http_method:
        http_method = event.get('httpMethod')

    if http_method == 'GET':
        return _list_documents(user_id, headers)

    if http_method == 'POST':
        return _request_upload(event, user_id, headers)

    if http_method == 'DELETE':
        return _delete_document(event, user_id, headers)

    return {'statusCode': 405, 'headers': headers, 'body': 'Method Not Allowed'}


# ── Private helpers ────────────────────────────────────────────────────────────

def _list_documents(user_id, headers):
    """List files, enriched with indexing status from DynamoDB."""
    prefix = f"{user_id}/"
    try:
        response = s3_client.list_objects_v2(Bucket=vault_bucket, Prefix=prefix)
        files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                filename   = obj['Key'].split('/')[-1]
                file_entry = {
                    'name':        filename,
                    'size':        obj['Size'],
                    'lastModified': str(obj['LastModified']),
                    'indexStatus': 'unknown',
                }
                if doc_status_table:
                    try:
                        status_resp = doc_status_table.get_item(
                            Key={'user_id': user_id, 'doc_key': obj['Key']}
                        )
                        status_item = status_resp.get('Item')
                        if status_item:
                            file_entry['indexStatus'] = status_item.get('status', 'unknown')
                            file_entry['chunkCount']  = int(status_item.get('chunk_count', 0))
                            file_entry['lastIndexed'] = status_item.get('last_updated', '')
                            if status_item.get('error'):
                                file_entry['indexError'] = status_item['error']
                    except Exception as status_err:
                        print(f"Status lookup error for {obj['Key']}: {status_err}")
                files.append(file_entry)
        return {'statusCode': 200, 'headers': headers, 'body': json.dumps({'files': files})}
    except ClientError as e:
        print(f"Error listing files: {e}")
        return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': 'Failed to list files'})}


def _request_upload(event, user_id, headers):
    """Generate a presigned S3 URL for direct client upload."""
    body      = json.loads(event.get('body', '{}'))
    filename  = body.get('filename', '')
    file_type = body.get('fileType', '')
    file_size = body.get('fileSize', 0)

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

    prefix = f"{user_id}/"
    try:
        list_resp     = s3_client.list_objects_v2(Bucket=vault_bucket, Prefix=prefix)
        current_count = list_resp.get('KeyCount', 0)
        if current_count >= MAX_FILES_PER_USER:
            return {
                'statusCode': 400, 'headers': headers,
                'body': json.dumps({'error': f'Maximum of {MAX_FILES_PER_USER} files allowed. Delete a file to upload a new one.'})
            }

        key = f"{user_id}/{filename}"
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': vault_bucket, 'Key': key, 'ContentType': file_type},
            ExpiresIn=300,
        )
        return {'statusCode': 200, 'headers': headers, 'body': json.dumps({'uploadUrl': presigned_url, 'key': key})}

    except Exception as e:
        print(f"Error in POST /documents: {e}")
        return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': str(e)})}


def _delete_document(event, user_id, headers):
    """Delete a file from S3 and purge all associated DynamoDB records."""
    params   = event.get('queryStringParameters', {})
    filename = params.get('filename')

    if not filename:
        return {'statusCode': 400, 'headers': headers, 'body': json.dumps({'error': 'Filename required'})}

    key = f"{user_id}/{filename}"
    try:
        s3_client.delete_object(Bucket=vault_bucket, Key=key)

        if chunks_table:
            try:
                resp       = chunks_table.query(
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

        if doc_status_table:
            try:
                doc_status_table.delete_item(Key={'user_id': user_id, 'doc_key': key})
            except Exception as status_err:
                print(f"Status delete error (non-fatal): {status_err}")

        return {'statusCode': 200, 'headers': headers, 'body': json.dumps({'message': 'Deleted'})}

    except ClientError as e:
        return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': str(e)})}
