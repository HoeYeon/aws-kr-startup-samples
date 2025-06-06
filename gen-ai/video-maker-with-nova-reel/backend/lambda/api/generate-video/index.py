import random
import boto3
import json
import os
import logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Load environment variables safely (add appropriate exception handling if needed)
MODEL_ID = os.environ.get('MODEL_ID')
S3_DESTINATION_BUCKET = os.environ.get('S3_DESTINATION_BUCKET')
VIDEO_MAKER_WITH_NOVA_REEL_PROCESS_TABLE_NAME = os.environ.get('VIDEO_MAKER_WITH_NOVA_REEL_PROCESS_TABLE_NAME')
AWS_REGION = 'us-east-1'  # Default region setting for Amazon Nova Reel

ddb_client = boto3.client('dynamodb')

def create_response(status_code, body):
    """
    Unified function for generating HTTP responses.
    """
    return {
        'statusCode': status_code,
        'body': json.dumps(body),
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
            'Access-Control-Allow-Methods': 'POST,OPTIONS'
        }
    }

def parse_body(body):
    """
    If the body is a string, parse it as JSON; if it's already a dict, return it directly.
    Return None if a parsing error occurs.
    """
    if not body:
        return None
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            logger.error("JSON parsing error: %s", e)
            return None
    return body

def lambda_handler(event, context):
    logger.info("Received event: %s", event)
    http_method = event.get('httpMethod', '')
    
    if http_method == 'OPTIONS':
        return create_response(200, {})
    
    if http_method != 'POST':
        return create_response(405, {'error': f"{http_method} Methods are not allowed."})
        
    # Parse and validate the request body
    body = event.get('body')
    parsed_body = parse_body(body)
    if not parsed_body:
        return create_response(400, {'error': 'Bad Request: A valid body is required.'})
        
    prompt = parsed_body.get('prompt')
    num_shots = parsed_body.get('num_shots', 2)  # 기본값을 2로 변경
    durationSeconds = max(num_shots * 6, 12)  # 최소 12초 보장

    if not prompt:
        return create_response(400, {'error': 'Bad Request: prompt field is required.'})
    
    # 이미지 데이터가 있는지 확인
    image_data = parsed_body.get('image_data')  # Base64로 인코딩된 이미지 데이터
    image_format = parsed_body.get('image_format', 'jpeg')  # 이미지 형식 (png 또는 jpeg)
    
    bedrock_runtime = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    
    # 기본 모델 입력 구성
    model_input = {
        "taskType": "MULTI_SHOT_AUTOMATED",
        "multiShotAutomatedParams": {
            "text": prompt
        },
        "videoGenerationConfig": {
            "durationSeconds": durationSeconds,
            "fps": 24,
            "dimension": "1280x720",
            "seed": random.randint(0, 2147483648)
        }
    }
    
    # 이미지 데이터가 있을 경우에만 images 필드 추가
    if image_data:
        model_input["multiShotAutomatedParams"]["images"] = [
            {
                "format": image_format,  # "png" 또는 "jpeg"
                "source": {
                    "bytes": image_data  # 이미 Base64로 인코딩된 문자열
                }
            }
        ]
    
    try:
        invocation = bedrock_runtime.start_async_invoke(
            modelId=MODEL_ID,
            modelInput=model_input,
            outputDataConfig={"s3OutputDataConfig": {"s3Uri": f"s3://{S3_DESTINATION_BUCKET}"}}
        )
    except Exception as e:
        logger.error("Bedrock asynchronous invocation error: %s", e)
        return create_response(500, {'error': f'Server error: Failed to initiate video generation request. {str(e)}'})
    
    invocation_arn = invocation.get("invocationArn")
    invocation_id = invocation_arn.split('/')[-1]
    
    print("Invocation ARN:", invocation_arn)
    print("Invocation ID:", invocation_id)

    if not invocation_arn:
        logger.error("invocationArn missing")
        return create_response(500, {'error': 'Server error: Failed to initiate video generation request.'})
        
    s3_prefix = invocation_arn.split('/')[-1]
    s3_location = f"s3://{S3_DESTINATION_BUCKET}/{s3_prefix}/output.mp4"

    # Save the invocation ARN to the DynamoDB table
    response = ddb_client.put_item(
        TableName=VIDEO_MAKER_WITH_NOVA_REEL_PROCESS_TABLE_NAME,
        Item={
            'invocation_id': {"S": invocation_id},
            'invocation_arn': {"S": invocation_arn},
            'prompt': {"S": prompt},
            'status': {"S": 'InProgress'},
            'location': {"S": s3_location},
            'updated_at': {"S": datetime.now().isoformat()},
            'created_at': {"S": datetime.now().isoformat()}
        }
    )
    
    return create_response(200, {
        'message': 'Video generation started',
        'invocationArn': invocation_arn,
        'location': f"{s3_location}/output.mp4"
    })