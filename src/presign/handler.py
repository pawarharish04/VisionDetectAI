"""
Presigned URL Lambda — src/presign/handler.py
==============================================
Returns a presigned S3 PUT URL so the browser uploads directly to
the images/ prefix.  Lambda never touches the binary data, keeping
costs near zero for large images.

GET /presign?filename=photo.jpg&contentType=image/jpeg

Response (200):
{
  "uploadUrl":   "<presigned PUT URL>",
  "imageKey":    "images/<uuid>/photo.jpg",
  "getUrl":      "<presigned GET URL>",
  "expiresIn":   300
}
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# ── Logging ──────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Constants ────────────────────────────────────────────────
BUCKET_NAME: str = os.environ["BUCKET_NAME"]
PRESIGN_EXPIRY_SECONDS: int = int(os.environ.get("PRESIGN_EXPIRY_SECONDS", "300"))
MAX_CONTENT_LENGTH_MB: int = int(os.environ.get("MAX_CONTENT_LENGTH_MB", "20"))

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

def is_valid_file(filename: str, content_type: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS and content_type.startswith("image/")

# S3 client with signature version 4 (required for presigned PUT)
s3_client = boto3.client(
    "s3",
    config=Config(signature_version="s3v4"),
)

# ── CORS headers ─────────────────────────────────────────────
CORS_HEADERS: dict = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Content-Type": "application/json",
}


# ── Helpers ───────────────────────────────────────────────────

def _error(status_code: int, message: str) -> dict:
    """Return a well-formed API Gateway error response."""
    logger.warning("Returning %s: %s", status_code, message)
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps({"error": message}),
    }


def _sanitize_filename(filename: str) -> str:
    """
    Strip directory traversal characters and limit length.
    Only allow [a-zA-Z0-9._-] to be safe in S3 keys.
    """
    import re
    base = os.path.basename(filename)
    safe = re.sub(r"[^\w.\-]", "_", base)
    return safe[:128]  # hard cap


def _build_image_key(filename: str) -> str:
    """
    Build a unique S3 key under the images/ prefix.
    Pattern: images/<date>/<uuid>/<filename>
    Keeps objects organized and avoids hot-partition collisions.
    """
    date_prefix = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    unique_id = str(uuid.uuid4())
    safe_name = _sanitize_filename(filename)
    return f"images/{date_prefix}/{unique_id}/{safe_name}"


def _generate_presigned_put(image_key: str, content_type: str) -> str:
    """
    Generate a presigned PUT URL.
    The browser must send Content-Type exactly as specified here,
    otherwise S3 will reject the request with 403.
    """
    url = s3_client.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": image_key,
            "ContentType": content_type,
        },
        ExpiresIn=PRESIGN_EXPIRY_SECONDS,
        HttpMethod="PUT",
    )
    return url


def _generate_presigned_get(image_key: str) -> str:
    """Generate a presigned GET URL valid for 1 hour for result polling."""
    return s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": BUCKET_NAME, "Key": image_key},
        ExpiresIn=3600,
        HttpMethod="GET",
    )


# ── Handler ───────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """
    API Gateway Lambda Proxy handler.

    Query parameters:
        filename    — original file name, e.g. "photo.jpg"  (required)
        contentType — MIME type, e.g. "image/jpeg"          (required)

    Returns a presigned PUT URL and the future S3 key so the
    frontend knows where to poll for detection results.
    """
    logger.info("Event received: %s", json.dumps(event))

    # ── Handle CORS preflight ──────────────────────────────
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    # ── Parse query parameters ─────────────────────────────
    query_params: dict = event.get("queryStringParameters") or {}

    filename: str | None = query_params.get("filename", "").strip()
    content_type: str | None = query_params.get("contentType", "").strip()

    # ── Validate ───────────────────────────────────────────
    if not filename:
        return _error(400, "Missing required query parameter: filename")

    if not content_type:
        return _error(400, "Missing required query parameter: contentType")

    if not is_valid_file(filename, content_type):
        return _error(
            400,
            "Invalid file or unsupported content type. "
            f"Allowed extensions: {sorted(ALLOWED_EXTENSIONS)} and must be an image MIME type.",
        )

    # ── Build S3 key ───────────────────────────────────────
    image_key = _build_image_key(filename)
    logger.info("Generated image key: %s", image_key)

    # ── Generate presigned URLs ────────────────────────────
    try:
        upload_url = _generate_presigned_put(image_key, content_type)
        get_url = _generate_presigned_get(image_key)
    except ClientError as exc:
        logger.exception("Failed to generate presigned URL")
        return _error(500, f"Could not generate presigned URL: {exc.response['Error']['Code']}")

    # ── Return response ────────────────────────────────────
    response_body = {
        "uploadUrl": upload_url,       # Browser PUTs file here
        "imageKey": image_key,         # Used to poll /results/{key}
        "getUrl": get_url,             # Optional: fetch the raw image back
        "bucket": BUCKET_NAME,
        "expiresIn": PRESIGN_EXPIRY_SECONDS,
        "maxFileSizeMb": MAX_CONTENT_LENGTH_MB,
        "instructions": {
            "method": "PUT",
            "headers": {
                "Content-Type": content_type
            },
            "note": (
                "PUT body = raw file bytes. "
                "Do NOT use multipart/form-data. "
                "Content-Type header must match exactly."
            ),
        },
    }

    logger.info(
        "Presigned URL generated for key=%s expires_in=%s",
        image_key,
        PRESIGN_EXPIRY_SECONDS,
    )

    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps(response_body),
    }
