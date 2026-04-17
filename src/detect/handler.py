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
bedrock_runtime = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
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


def _detect_ppe(image: dict) -> dict[str, Any]:
    """
    Detect Personal Protective Equipment.
    Returns Persons and Summary dicts.
    """
    resp = rekognition.detect_protective_equipment(
        Image=image,
        SummarizationAttributes={
            'MinConfidence': 80,
            'RequiredEquipmentTypes': ['HEAD_COVER', 'HAND_COVER']
        }
    )
    return {
        "Persons": resp.get("Persons", []),
        "Summary": resp.get("Summary", {})
    }


# ─────────────────────────────────────────────────────────────
# Parallel execution
# ─────────────────────────────────────────────────────────────

_TASKS = {
    "labels":      _detect_labels,
    "text":        _detect_text,
    "moderation":  _detect_moderation,
    "ppe":         _detect_ppe,
}


def _run_rekognition_parallel(image: dict) -> dict[str, Any]:
    """
    Fire all four Rekognition calls simultaneously.
    Uses a thread pool (not asyncio) because boto3 is I/O bound but not natively async.

    Returns a dict of {task_name: result_list}.
    Raises the first encountered exception so the caller can mark the record as failed.
    """
    results: dict[str, Any] = {}
    errors:  list[str]      = []

    with ThreadPoolExecutor(max_workers=4) as executor:
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
        
        # ── Extract PPE Summary ──
        ppe_data = result.get("ppe", {})
        summary = ppe_data.get("Summary", {})
        persons_with_req = summary.get("PersonsWithRequiredEquipment", [])
        persons_without_req = summary.get("PersonsWithoutRequiredEquipment", [])
        persons_indet = summary.get("PersonsIndeterminate", [])
        
        # Count totals
        total_detected = len(ppe_data.get("Persons", []))
        total_missed = len(persons_without_req)
        
        item["persons_detected"] = total_detected
        item["persons_without_ppe"] = total_missed
        item["compliance_status"] = "FAIL" if total_missed > 0 else ("PASS" if total_detected > 0 else "N/A")
        
        # ── Compute PPE Reasoning ──
        reasoning_list = []
        # These correspond to what we requested in 'RequiredEquipmentTypes'
        required_types = ['HEAD_COVER', 'HAND_COVER']
        for p in ppe_data.get("Persons", []):
            pid = p.get("Id", "Unknown")
            
            if pid in persons_without_req:
                detected_gear = set()
                # Analyze body parts to see what *is* there
                for bp in p.get("BodyParts", []):
                    for eq in bp.get("EquipmentDetections", []):
                        if eq.get("CoversBodyPart", {}).get("Value", False):
                            detected_gear.add(eq.get("Type"))
                
                # Determine what is missing based on what is required
                missing = [r for r in required_types if r not in detected_gear]
                readable_missing = []
                for m in missing:
                    if m == "HEAD_COVER": readable_missing.append("Hard Hat")
                    elif m == "HAND_COVER": readable_missing.append("Gloves")
                    elif m == "FACE_COVER": readable_missing.append("Mask")
                    else: readable_missing.append(m)
                
                if readable_missing:
                    reasoning_list.append(f"Person {pid} is missing: {', '.join(readable_missing)}")
                else:
                    reasoning_list.append(f"Person {pid} is missing required PPE.")

        if reasoning_list:
            item["ppe_reasoning"] = reasoning_list

        # ── Bedrock Generative AI Final Verdict ──
        # Build prompt from object labels, moderation, and PPE results to get a comprehensive verdict.
        try:
            detected_labels = [lbl.get("Name") for lbl in result.get("labels", []) if lbl.get("Confidence", 0) > 75][:10]
            moderation_labels = [lbl.get("Name") for lbl in result.get("moderation", [])]
            
            prompt = (
                "You are an expert AI visual analyst and safety reviewer. "
                "Review the following AI JSON detection data from an image. "
                "Provide a 'Final Verdict' in 2 or 3 concise sentences describing the scene. Start your response with 'Final Verdict: '. "
                "If persons are detected, assess their safety and PPE compliance. "
                "If no persons are present, analyze the general objects, layout, and any moderation flags to provide a comprehensive summary of the context and any potential hazards."
            )
            data_context = {
                "detected_objects": detected_labels,
                "moderation_flags": moderation_labels,
                "persons_total": total_detected,
                "persons_missing_ppe": total_missed,
                "ppe_warnings": reasoning_list
            }
            prompt += f"\nDATA:\n{json.dumps(data_context)}\n\nReport:"
            
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 150,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            })
            
            bedrock_response = bedrock_runtime.invoke_model(
                modelId="anthropic.claude-3-haiku-20240307-v1:0",
                contentType="application/json",
                accept="application/json",
                body=body
            )
            response_body = json.loads(bedrock_response['body'].read())
            report_text = response_body['content'][0]['text']
            item["ai_safety_report"] = report_text.strip()
            logger.info("Generated AI Safety Report successfully.")
        except Exception as bed_err:
            logger.error("Failed to generate Bedrock report: %s", str(bed_err))
            # Fallback final verdict if Bedrock fails or isn't enabled
            if total_missed > 0:
                item["ai_safety_report"] = f"Final Verdict: System detected {total_missed} person(s) missing specific equipment (e.g., head or hand covers). Safety or compliance may be compromised."
            elif total_detected > 0:
                item["ai_safety_report"] = f"Final Verdict: {total_detected} person(s) detected with expected equipment. No immediate equipment violations found."
            else:
                item["ai_safety_report"] = "Final Verdict: No persons detected. Image processed successfully for objects and general labels."

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
    
    # ── Real-Time SNS / Email Alerts ──
    # Trigger an alert if there are missing PPE requirements or moderation/critical labels
    if total_missed > 0 or result.get("moderation"):
        failure_msg = {
            "Alert": "CRITICAL SAFETY FAILURE DETECTED",
            "Image": image_key,
            "ComplianceStatus": "FAIL",
            "PersonsWithoutPPE": total_missed,
            "AI_Verdict": item.get("ai_safety_report", "N/A"),
            "Reasoning": reasoning_list,
            "Timestamp": now
        }
        try:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject="🚨 SAFETY ALERT: Critical Failure Detected",
                Message=json.dumps(failure_msg, indent=2)
            )
            logger.info("Published Critical Safety Alert to SNS.")
        except Exception as sns_err:
            logger.error("SNS publish failed: %s", sns_err)


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
