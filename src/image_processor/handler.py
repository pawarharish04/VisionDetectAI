# src/image_processor/handler.py
"""
Kinesis-triggered Lambda: receives JPEG frames published by the video capture
client, runs Rekognition (labels + text + moderation) in parallel, stores
enriched metadata in DynamoDB, uploads the raw frame to S3, and fires SNS
alerts when watch-list objects are detected.
"""

import base64
import json
import logging
import os
import time
import uuid
import decimal
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── AWS clients (module-level so they are reused across warm invocations) ──
rekog  = boto3.client("rekognition")
s3     = boto3.client("s3")
dynamo = boto3.resource("dynamodb")
sns    = boto3.client("sns")

# ── Environment ────────────────────────────────────────────────────────────
S3_BUCKET       = os.environ["S3_BUCKET"]
DDB_TABLE       = os.environ["DDB_TABLE"]
SNS_TOPIC_ARN   = os.environ["SNS_TOPIC_ARN"]
REKOG_MAX_LABELS = int(os.environ.get("REKOG_MAX_LABELS", 20))
REKOG_MIN_CONF   = float(os.environ.get("REKOG_MIN_CONF", 50.0))
WATCH_LIST       = [
    l.strip()
    for l in os.environ.get("LABEL_WATCH_LIST", "").split(",")
    if l.strip()
]
WATCH_MIN_CONF  = float(os.environ.get("LABEL_WATCH_MIN_CONF", 85.0))
TTL_DAYS        = int(os.environ.get("TTL_DAYS", 30))
INGESTION_API_KEY = os.environ.get("INGESTION_API_KEY")


# ── Entry point ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """Process a single frame POSTed via Function URL."""
    try:
        # ── Step 0: Security Check ──────────────────────────────────────────
        if INGESTION_API_KEY:
            headers = event.get("headers", {})
            # Function URL headers can be lowercase
            request_key = headers.get("x-api-key") or headers.get("X-Api-Key")
            if request_key != INGESTION_API_KEY:
                logger.warning("Unauthorized ingestion attempt: invalid API Key")
                return {
                    "statusCode": 403,
                    "body": json.dumps({"status": "error", "message": "Unauthorized"})
                }

        body = event.get("body", "{}")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        
        frame_package = json.loads(body)
        process_frame(frame_package)
        
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "ok", "processed": 1})
        }
    except Exception as exc:
        logger.error("Failed to process frame: %s", exc, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "message": str(exc)})
        }


# ── Core processing ────────────────────────────────────────────────────────

def process_frame(frame_package: dict) -> None:
    """
    1. Decode the JPEG bytes.
    2. Upload raw frame to S3 under frames/.
    3. Run Rekognition (labels, text, moderation) in parallel.
    4. Check watch list → publish SNS if triggered.
    5. Persist enriched metadata to DynamoDB.
    """
    image_bytes = base64.b64decode(frame_package["image_data"])
    source      = frame_package.get("source", "video-stream")
    cap_ts      = frame_package.get("capture_timestamp", int(time.time() * 1000))
    proc_ts     = int(time.time() * 1000)
    image_key   = f"frames/{proc_ts}_{uuid.uuid4().hex[:8]}.jpg"

    # ── Step 1: Upload raw frame to S3 ────────────────────────────────────
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=image_key,
            Body=image_bytes,
            ContentType="image/jpeg",
            Metadata={
                "source": str(source),
                "capture_timestamp": str(cap_ts),
            },
        )
    except ClientError as exc:
        logger.error("S3 upload failed for %s: %s", image_key, exc)
        raise

    # ── Step 2: Parallel Rekognition calls ────────────────────────────────
    def detect_labels():
        return rekog.detect_labels(
            Image={"Bytes": image_bytes},
            MaxLabels=REKOG_MAX_LABELS,
            MinConfidence=REKOG_MIN_CONF,
        )

    def detect_text():
        return rekog.detect_text(Image={"Bytes": image_bytes})

    def detect_moderation():
        return rekog.detect_moderation_labels(
            Image={"Bytes": image_bytes},
            MinConfidence=REKOG_MIN_CONF,
        )

    tasks = {
        "labels":     detect_labels,
        "text":       detect_text,
        "moderation": detect_moderation,
        "ppe":        lambda: rekog.detect_protective_equipment(Image={"Bytes": image_bytes})
    }
    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {executor.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                results[name] = future.result()
            except ClientError as exc:
                logger.warning("Rekognition %s failed: %s", name, exc)
                results[name] = {}

    labels     = results.get("labels",     {}).get("Labels",            [])
    texts      = results.get("text",       {}).get("TextDetections",    [])
    mod_labels = results.get("moderation", {}).get("ModerationLabels",  [])
    ppe_raw    = results.get("ppe",        {})

    # ── Step 3: PPE Compliance Logic ──────────────────────────────────────
    persons = ppe_raw.get("Persons", [])
    ppe_summary = ppe_raw.get("Summary", {})
    if not persons:
        ppe_compliance_status = "N/A"
        persons_without_ppe = 0
    else:
        persons_without_ppe = len(ppe_summary.get("PersonsWithoutRequiredEquipment", []))
        ppe_compliance_status = "COMPLIANT" if persons_without_ppe == 0 else "NON_COMPLIANT"

    # ── Step 4: Watch-list check & SNS alert ──────────────────────────────
    triggered = [
        lbl for lbl in labels
        if lbl["Name"] in WATCH_LIST and lbl["Confidence"] >= WATCH_MIN_CONF
    ]
    if triggered:
        _publish_alert(triggered, image_key)

    # ── Step 5: Persist to DynamoDB ───────────────────────────────────────
    table = dynamo.Table(DDB_TABLE)
    item  = {
        "imageKey":            image_key,   # matches primary HASH key
        "timestamp":           proc_ts,     # matches primary RANGE key
        "status":              "COMPLETE",  # triggers AnnotateFunction stream
        "statusPk":            "COMPLETE",  # used for GSI query
        "processed_timestamp": proc_ts,     # used for GSI sorting
        "capture_timestamp":   cap_ts,
        "source":              source,
        # Use PascalCase for consistency with DetectFunction and AnnotateFunction
        "labels": labels,
        "text_detections": [
            t["DetectedText"] for t in texts if t.get("Type") == "LINE"
        ],
        "moderation_labels": [m["Name"] for m in mod_labels],
        "ppe": ppe_raw,
        "ppe_raw": ppe_raw,
        "ppe_compliance_status": ppe_compliance_status,
        "persons_without_ppe": persons_without_ppe,
        "watch_list_triggered": [lbl["Name"] for lbl in triggered],
        "label_count":  len(labels),
        "ttl":          int(time.time()) + TTL_DAYS * 24 * 3600,
    }

    try:
        parsed_item = json.loads(json.dumps(item), parse_float=decimal.Decimal)
        table.put_item(Item=parsed_item)
    except ClientError as exc:
        logger.error("DynamoDB put_item failed: %s", exc)
        raise

    logger.info(
        "Processed frame: key=%s labels=%d watch_alerts=%d",
        image_key, len(labels), len(triggered),
    )


def _publish_alert(triggered: list, image_key: str) -> None:
    """Publish an SNS alert listing the watch-list objects found in a frame."""
    lines = [f"  • {obj['Name']} ({obj['Confidence']:.1f}%)" for obj in triggered]
    message = (
        "⚠️  VisionDetectAI — Watch-list objects detected in live video!\n\n"
        + "\n".join(lines)
        + f"\n\nFrame S3 key: {image_key}"
    )
    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="VisionDetectAI Live-Stream Alert",
            Message=message,
        )
    except ClientError as exc:
        # Non-fatal — log and continue
        logger.error("SNS publish failed: %s", exc)
