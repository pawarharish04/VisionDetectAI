"""
Object Detection Lambda — src/detect/handler.py
================================================
Triggered by s3:ObjectCreated on the images/ prefix.

Calls three Rekognition APIs in parallel, then writes a combined
result record to DynamoDB with a 30-day TTL.

DynamoDB schema
---------------
  imageKey (PK, string)  — "images/2024/01/15/<uuid>/photo.jpg"
  timestamp (SK, number) — Unix epoch seconds (int)
  result    (string)     — JSON blob of all three Rekognition responses
  ttl       (number)     — Unix epoch, auto-deleted after 30 days
  status    (string)     — "complete" | "failed"
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ── Logging ──────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Environment ───────────────────────────────────────────────
TABLE_NAME: str = os.environ["TABLE_NAME"]
SNS_TOPIC_ARN: str = os.environ.get("SNS_TOPIC_ARN", "")
HIGH_CONFIDENCE_THRESHOLD: float = float(
    os.environ.get("HIGH_CONFIDENCE_THRESHOLD", "90")
)
TTL_DAYS: int = int(os.environ.get("TTL_DAYS", "30"))

# ── AWS clients (module-level = reused across warm invocations) ─
rekognition = boto3.client("rekognition")
dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")
table = dynamodb.Table(TABLE_NAME)

# ── Important Labels Whitelist ───────────────────────────────
# ── Important Labels Whitelist ───────────────────────────────
IMPORTANT_LABELS = {"Weapon", "Fire", "Explicit Nudity"}


def send_alert_if_needed(result: dict, image_key: str) -> None:
    high_conf = []

    for label in result.get("labels", []):
        if label.get("Confidence", 0) >= 90:
            high_conf.append({
                "Name": label["Name"],
                "Confidence": round(label["Confidence"], 2)
            })

    # 🚨 RULES
    should_alert = (
        len(high_conf) >= 2 or
        any(l["Name"] in IMPORTANT_LABELS for l in high_conf)
    )

    if not should_alert:
        return

    message = {
        "imageKey": image_key,
        "alerts": high_conf
    }

    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="🚨 High Confidence Detection Alert",
            Message=json.dumps(message, indent=2)
        )
        logger.info("Published SNS alert for %s", image_key)
    except Exception as e:
        logger.error("SNS publish failed: %s", e)
# ─────────────────────────────────────────────────────────────
# Rekognition helpers
# ─────────────────────────────────────────────────────────────

def _detect_labels(image: dict) -> list[dict]:
    """
    Detect objects, scenes, and concepts.
    Returns the Labels list — each item has Name, Confidence, Instances, Parents.
    """
    resp = rekognition.detect_labels(
        Image=image,
        MaxLabels=20,
        MinConfidence=HIGH_CONFIDENCE_THRESHOLD,
    )
    return resp["Labels"]


def _detect_text(image: dict) -> list[dict]:
    """
    Detect printed and handwritten text.
    Returns TextDetections list — each item has DetectedText, Type, Confidence.
    """
    resp = rekognition.detect_text(Image=image)
    return resp["TextDetections"]


def _detect_moderation(image: dict) -> list[dict]:
    """
    Detect unsafe content.
    Returns ModerationLabels — each item has Name, ParentName, Confidence.
    """
    resp = rekognition.detect_moderation_labels(Image=image)
    return resp["ModerationLabels"]


# ─────────────────────────────────────────────────────────────
# Parallel execution
# ─────────────────────────────────────────────────────────────

_TASKS = {
    "labels":      _detect_labels,
    "text":        _detect_text,
    "moderation":  _detect_moderation,
}


def _run_rekognition_parallel(image: dict) -> dict[str, Any]:
    """
    Fire all three Rekognition calls simultaneously.
    Uses a thread pool (not asyncio) because boto3 is I/O bound but not natively async.

    Returns a dict of {task_name: result_list}.
    Raises the first encountered exception so the caller can mark the record as failed.
    """
    results: dict[str, Any] = {}
    errors:  list[str]      = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(fn, image): name
            for name, fn in _TASKS.items()
        }

        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
                logger.info(
                    "Rekognition %s → %d items",
                    name,
                    len(results[name]),
                )
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                msg  = exc.response["Error"]["Message"]
                logger.error("Rekognition %s failed: %s — %s", name, code, msg)
                errors.append(f"{name}: {code} — {msg}")

    if errors:
        raise RuntimeError(
            f"One or more Rekognition calls failed:\n" + "\n".join(errors)
        )

    return results


# ─────────────────────────────────────────────────────────────
# DynamoDB write
# ─────────────────────────────────────────────────────────────

def _write_to_dynamodb(
    image_key: str,
    result: dict[str, Any],
    status: str = "complete",
    error_msg: str | None = None,
) -> None:
    """
    Upsert a detection record into DetectionResults.

    The sort key is the current epoch so multiple detections of the
    same image can coexist (e.g. reprocessing).
    """
    now = int(time.time())
    item: dict[str, Any] = {
        "imageKey":  image_key,        # PK
        "timestamp": now,              # SK  — epoch seconds
        "status":    status,
        "statusPk":  f"STATUS#{status}",  # GSI1 partition key
        "ttl":       now + TTL_DAYS * 86_400,
    }

    if result:
        item["result"] = json.dumps(result, default=str)

    if error_msg:
        item["errorMessage"] = error_msg

    # Flatten top-level label names for easy DynamoDB scan / GSI later
    if "labels" in result:
        item["labelNames"] = [lbl["Name"] for lbl in result["labels"]]
        item["topLabel"] = (
            result["labels"][0]["Name"] if result["labels"] else None
        )

    table.put_item(Item=item)
    logger.info(
        "DynamoDB write OK — imageKey=%s status=%s label_count=%d",
        image_key,
        status,
        len(result.get("labels", [])),
    )


# ─────────────────────────────────────────────────────────────
# Handler
# ─────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """
    S3 event handler — processes each record in the batch.

    Typical batch size is 1 (S3 → Lambda), but we iterate defensively
    in case S3 batching is enabled.

    Returns a summary dict (not used by S3 trigger, but useful for
    manual invocation and testing).
    """
    processed: list[str] = []
    failed:    list[str] = []

    for record in event.get("Records", []):
        bucket    = record["s3"]["bucket"]["name"]
        image_key = record["s3"]["object"]["key"]

        # Safety: only process images/ prefix
        if not image_key.startswith("images/"):
            logger.warning("Skipping non-images/ key: %s", image_key)
            continue

        image_ref = {"S3Object": {"Bucket": bucket, "Name": image_key}}
        logger.info("Processing: s3://%s/%s", bucket, image_key)

        try:
            # ── Parallel Rekognition ──────────────────────────
            result = _run_rekognition_parallel(image_ref)

            # ── Persist to DynamoDB ───────────────────────────
            _write_to_dynamodb(image_key, result, status="complete")

            # ── SNS Alerts ────────────────────────────────────
            if SNS_TOPIC_ARN:
                send_alert_if_needed(result, image_key)

            processed.append(image_key)

        except Exception as exc:  # noqa: BLE001
            logger.exception("Detection failed for %s", image_key)
            # Write a failed record so downstream can identify stuck images
            _write_to_dynamodb(
                image_key,
                result={},
                status="failed",
                error_msg=str(exc),
            )
            failed.append(image_key)

    summary = {
        "processed": processed,
        "failed":    failed,
        "total":     len(processed) + len(failed),
    }
    logger.info("Batch summary: %s", summary)
    return summary
