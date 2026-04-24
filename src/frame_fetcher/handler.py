# src/frame_fetcher/handler.py
"""
API Gateway GET /enrichedframe → returns the N most recent enriched frames
from DynamoDB together with short-lived S3 pre-signed URLs, so the Web UI
can render the live video feed without exposing credentials.
"""

import json
import logging
import os
import time

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── AWS clients ────────────────────────────────────────────────────────────
dynamo = boto3.resource("dynamodb")
s3     = boto3.client("s3")

# ── Environment ────────────────────────────────────────────────────────────
S3_BUCKET         = os.environ["S3_BUCKET"]
DDB_TABLE         = os.environ["DDB_TABLE"]
DDB_GSI_NAME      = os.environ.get("DDB_GSI_NAME", "processed-timestamp-index")
FETCH_HORIZON_HRS = int(os.environ.get("FETCH_HORIZON_HRS", 24))
FETCH_LIMIT       = int(os.environ.get("FETCH_LIMIT", 10))
PRESIGNED_EXPIRY  = int(os.environ.get("PRESIGNED_URL_EXPIRY", 1800))

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Api-Key,Authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}


# ── Entry point ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """Return recent enriched frames with pre-signed S3 URLs."""
    # Handle CORS pre-flight
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    try:
        items = _fetch_recent_frames()
        enriched = _attach_presigned_urls(items)
        return {
            "statusCode": 200,
            "headers":    CORS_HEADERS,
            "body":       json.dumps(enriched, default=str),
        }
    except Exception as exc:
        logger.error("frame_fetcher error: %s", exc, exc_info=True)
        return {
            "statusCode": 500,
            "headers":    CORS_HEADERS,
            "body":       json.dumps({"error": "Internal server error"}),
        }


# ── Helpers ────────────────────────────────────────────────────────────────

def _fetch_recent_frames() -> list:
    """
    Scan DynamoDB for frames processed within the horizon window.
    Returns items sorted newest-first, capped at FETCH_LIMIT.

    Note: Scan is fine for the throughput levels of a video-stream demo
    (tens of rows per minute). Switch to a GSI Query when throughput grows.
    """
    table       = dynamo.Table(DDB_TABLE)
    horizon_ms  = int((time.time() - FETCH_HORIZON_HRS * 3600) * 1000)

    try:
        # Using GSI Query: much more efficient than Scan
        response = table.query(
            IndexName=DDB_GSI_NAME,
            KeyConditionExpression="statusPk = :pk AND processed_timestamp > :horizon",
            ExpressionAttributeValues={
                ":pk":      "COMPLETE",
                ":horizon": horizon_ms,
            },
            ScanIndexForward=False,   # Sort descending (newest first)
            Limit=FETCH_LIMIT,
        )
    except ClientError as exc:
        logger.error("DynamoDB query failed: %s", exc)
        raise

    items = response.get("Items", [])
    return items


def _attach_presigned_urls(items: list) -> list:
    """Generate a fresh pre-signed GET URL for each frame image."""
    enriched = []
    for item in items:
        image_key = item.get("imageKey", "")
        presigned_url = ""
        if image_key:
            try:
                presigned_url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": S3_BUCKET, "Key": image_key},
                    ExpiresIn=PRESIGNED_EXPIRY,
                )
            except ClientError as exc:
                logger.warning("Could not generate presigned URL for %s: %s", image_key, exc)

        enriched.append({**item, "presigned_url": presigned_url})

    return enriched
