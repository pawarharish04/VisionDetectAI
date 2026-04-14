"""
Annotate Lambda — src/annotate/handler.py
=========================================
Triggered by DynamoDB Streams (INSERT events) from DetectionResults.

Fetches the original image, uses Pillow to draw bounding boxes and
labels from the Rekognition results, and uploads the annotated image
to the results/ prefix. Finally, updates the DynamoDB item with the
annotatedKey and annotatedUrl.
"""

from __future__ import annotations

import io
import json
import logging
import os
from typing import Any

import boto3
from PIL import Image, ImageDraw, ImageFont

# ── Logging ──────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Environment ───────────────────────────────────────────────
BUCKET_NAME: str = os.environ["BUCKET_NAME"]
TABLE_NAME: str = os.environ["TABLE_NAME"]
AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")

# ── AWS clients ───────────────────────────────────────────────
s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)


def process_record(record: dict) -> None:
    """Process a single DynamoDB Stream record."""
    if record.get("eventName") != "INSERT":
        return

    new_image = record.get("dynamodb", {}).get("NewImage", {})
    
    # DynamoDB Stream uses typed formats e.g. {"S": "complete"}
    status = new_image.get("status", {}).get("S")
    if status != "complete":
        return

    image_key = new_image.get("imageKey", {}).get("S")
    timestamp = new_image.get("timestamp", {}).get("N")
    result_str = new_image.get("result", {}).get("S", "{}")

    if not image_key or not timestamp:
        logger.warning("Missing imageKey or timestamp, skipping.")
        return

    try:
        result_blob = json.loads(result_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse result JSON for %s", image_key)
        return

    labels = result_blob.get("labels", [])
    
    # Pre-check if any bounding boxes exist
    has_boxes = any(
        "BoundingBox" in instance 
        for label in labels 
        for instance in label.get("Instances", [])
    )
    if not has_boxes:
        logger.info("No bounding boxes found for %s, using original image as result.", image_key)
        original_url = f"https://{BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{image_key}"
        try:
            table.update_item(
                Key={"imageKey": image_key, "timestamp": int(timestamp)},
                UpdateExpression="SET annotatedKey = :ak, annotatedUrl = :au",
                ExpressionAttributeValues={":ak": image_key, ":au": original_url}
            )
        except Exception as e:
            logger.error("Failed to update DDB for %s: %s", image_key, e)
        return

    # ── Fetch original image ────────────────────────────────
    try:
        resp = s3_client.get_object(Bucket=BUCKET_NAME, Key=image_key)
        image_bytes = resp["Body"].read()
        content_type = resp.get("ContentType", "image/jpeg")
    except Exception as e:
        logger.error("Failed to download image %s from S3: %s", image_key, e)
        return

    # ── Annotate ────────────────────────────────────────────
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Convert to RGB to ensure we can draw colored boxes safely
            if img.mode != "RGB":
                img = img.convert("RGB")
                
            draw = ImageDraw.Draw(img)
            width, height = img.size
            font = ImageFont.load_default()

            for label in labels:
                label_name = label.get("Name", "Unknown")
                confidence = label.get("Confidence", 0.0)
                
                for instance in label.get("Instances", []):
                    bbox = instance.get("BoundingBox")
                    if not bbox:
                        continue
                        
                    left = bbox["Left"] * width
                    top = bbox["Top"] * height
                    box_w = bbox["Width"] * width
                    box_h = bbox["Height"] * height
                    right = left + box_w
                    bottom = top + box_h
                    
                    # Draw Bounding Box
                    draw.rectangle([left, top, right, bottom], outline="red", width=3)
                    
                    # Draw Label Name + Confidence
                    text = f"{label_name} ({confidence:.1f}%)"
                    
                    # Try to use textbbox for background rect, fallback if older Pillow
                    if hasattr(draw, "textbbox"):
                        t_left, t_top, t_right, t_bottom = draw.textbbox((left, max(0, top - 15)), text, font=font)
                    else:
                        t_left, t_top, t_right, t_bottom = left, max(0, top - 15), left + 100, max(0, top)
                        
                    draw.rectangle([t_left, t_top, t_right, t_bottom], fill="red")
                    draw.text((t_left, t_top), text, fill="white", font=font)

            out_buffer = io.BytesIO()
            img.save(out_buffer, format="JPEG")
            out_bytes = out_buffer.getvalue()
    except Exception as e:
        logger.error("Image processing failed for %s: %s", image_key, e)
        return

    # ── Upload Annotated Image ─────────────────────────────
    annotated_key = image_key.replace("images/", "results/", 1)
    if not annotated_key.startswith("results/"):
        annotated_key = f"results/{image_key}"
        
    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=annotated_key,
            Body=out_bytes,
            ContentType="image/jpeg"
        )
    except Exception as e:
        logger.error("Failed to upload annotated image %s: %s", annotated_key, e)
        return

    # ── Update DynamoDB ────────────────────────────────────
    annotated_url = f"https://{BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{annotated_key}"
    
    try:
        table.update_item(
            Key={
                "imageKey": image_key,
                "timestamp": int(timestamp)
            },
            UpdateExpression="SET annotatedKey = :ak, annotatedUrl = :au",
            ExpressionAttributeValues={
                ":ak": annotated_key,
                ":au": annotated_url
            }
        )
        logger.info("Successfully updated DDB for %s with annotation details", image_key)
    except Exception as e:
        logger.error("Failed to update DDB for %s: %s", image_key, e)


def lambda_handler(event: dict, context: Any) -> dict:
    records = event.get("Records", [])
    logger.info("Processing %d stream records", len(records))
    
    processed = 0
    for record in records:
        try:
            process_record(record)
            processed += 1
        except Exception as e:
            logger.exception("Error processing stream record")
            
    return {"processed": processed}
