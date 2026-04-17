"""
History Lambda — src/history/handler.py
================================================
Fetches the most recent detection results from DynamoDB to build a Compliance Dashboard.
"""

import json
import logging
import os
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("TABLE_NAME")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME) if TABLE_NAME else None

def lambda_handler(event, context):
    try:
        # We query the GSI1 Index: statusPk="STATUS#complete"
        # Sorted by timestamp (DESC)
        
        response = table.query(
            IndexName='GSI1',
            KeyConditionExpression=Key('statusPk').eq('STATUS#complete'),
            ScanIndexForward=False, # Descending order
            Limit=50 # Top 50 recent results
        )
        
        items = response.get('Items', [])
        
        # We don't want to send the HUGE raw 'result' JSON string for each item in the history list.
        # Let's extract just what we need: imageKey, timestamp, compliance_status, ai_safety_report, topLabel
        history = []
        for i in items:
            history.append({
                "imageKey": i.get("imageKey"),
                "timestamp": int(i.get("timestamp", 0)),
                "compliance_status": i.get("compliance_status", "N/A"),
                "ai_safety_report": i.get("ai_safety_report", ""),
                "topLabel": i.get("topLabel", ""),
                "persons_detected": int(i.get("persons_detected", 0)),
                "persons_without_ppe": int(i.get("persons_without_ppe", 0))
            })
            
        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json"
            },
            "body": json.dumps({"history": history})
        }
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        return {
            "statusCode": 500,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json"
            },
            "body": json.dumps({"error": str(e)})
        }
