"""
Results API — src/results/handler.py
======================================
Returns the latest detection results for a given imageKey.
If no record exists, returns 404 (handled by frontend polling).

GET /results/{imageKey+} -> decodes the proxy+ param

DynamoDB Schema
---------------
  imageKey (PK)
  timestamp (SK)
"""
import json
import logging
import os
import urllib.parse

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ["TABLE_NAME"]
BUCKET_NAME = os.environ["BUCKET_NAME"]
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)
s3_client = boto3.client("s3")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Content-Type": "application/json"
}

def lambda_handler(event: dict, context) -> dict:
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    # API Gateway {proxy+} is accessible in pathParameters
    path_params = event.get("pathParameters") or {}
    
    # We map /results/{proxy+} in API Gateway
    # Example: /results/images/2024/...
    # But API gateway path parameters are URL encoded if clients send them like that
    raw_key = path_params.get("proxy")
    if not raw_key:
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Missing imageKey in root path"})
        }

    # API GW sometimes provides decoded forms, sometimes not, unquote cleanly
    image_key = urllib.parse.unquote(raw_key)

    try:
        # Since timestamp is SK, we must use Query to get the latest record
        # A single imageKey could theoretically correspond to multiple runs if reprocessed
        resp = table.query(
            KeyConditionExpression=Key("imageKey").eq(image_key),
            ScanIndexForward=False, # sort descending (latest timestamp first)
            Limit=1
        )
        
        items = resp.get("Items", [])
        if not items:
            return {
                "statusCode": 404,
                "headers": CORS_HEADERS,
                "body": json.dumps({"status": "pending"})
            }
            
        item = items[0]
        
        # S3 is private, so generate a presigned URL if annotatedKey exists
        if item.get("annotatedKey"):
            try:
                presigned_url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': BUCKET_NAME, 'Key': item["annotatedKey"]},
                    ExpiresIn=3600
                )
                item["annotatedUrl"] = presigned_url
            except Exception as e:
                logger.error(f"Failed to generate presigned URL for {item['annotatedKey']}: {e}")

        # Serialize decimal.Decimal to int/float for JSON
        class DecimalEncoder(json.JSONEncoder):
            def default(self, obj):
                import decimal
                if isinstance(obj, decimal.Decimal):
                    return float(obj)
                return super(DecimalEncoder, self).default(obj)

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps(item, cls=DecimalEncoder)
        }
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching {image_key}: {e}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Failed to fetch results"})
        }
