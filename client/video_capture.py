#!/usr/bin/env python3
"""
client/video_capture.py
=======================
VisionDetectAI — Local video capture client.

Captures frames from a webcam (0) or an MJPEG/RTSP URL, encodes each frame
as a JPEG, and publishes it directly to the ImageProcessor Lambda Function URL
via HTTP POST requests.

Requirements
------------
    pip install opencv-python requests

Usage examples
--------------
    # Laptop webcam, send every 10th frame
    python client/video_capture.py --url "https://<your-lambda-id>.lambda-url.<region>.on.aws/" --source 0 --rate 10

    # IP camera MJPEG stream, send every 20th frame
    python client/video_capture.py --url "https://..." --source "http://192.168.1.10/video" --rate 20
"""

import argparse
import base64
import json
import logging
import signal
import sys
import time

import cv2
import requests

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("video_capture")

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_INGESTION_URL = ""
JPEG_QUALITY          = 85     # 0–100; lower → smaller payload, faster upload
MAX_FRAME_BYTES       = 6_000_000  # Function URL limit is 6MB (buffered)
_running              = True


def _sigint_handler(sig, frame):
    global _running
    log.info("Interrupt received — shutting down.")
    _running = False


signal.signal(signal.SIGINT,  _sigint_handler)
signal.signal(signal.SIGTERM, _sigint_handler)


# ── Main capture loop ──────────────────────────────────────────────────────

def capture_and_send(
    source,
    ingestion_url: str,
    capture_rate: int = 10,
    rotate: int | None = None,
    jpeg_quality: int = JPEG_QUALITY,
) -> None:
    """
    Open *source*, read frames in a loop, and push every *capture_rate*-th
    frame to Kinesis.

    Parameters
    ----------
    source        : int (0 for default webcam) or str URL
    stream_name   : Kinesis stream name
    partition_key : Kinesis partition key
    region        : AWS region
    capture_rate  : send 1 frame every N frames captured
    rotate        : optional rotation in degrees (90 or 180)
    jpeg_quality  : JPEG encoding quality 0-100
    """
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        log.error("Cannot open video source: %s", source)
        sys.exit(1)

    fps_hint = cap.get(cv2.CAP_PROP_FPS) or 30
    log.info(
        "▶  Streaming  source=%s  url=%s...  rate=1/%d  fps≈%.0f",
        source, ingestion_url[:30], capture_rate, fps_hint,
    )

    frame_count  = 0
    sent_count   = 0
    error_count  = 0
    session_start = time.time()

    try:
        while _running:
            ret, frame = cap.read()
            if not ret:
                log.warning("Frame grab failed — stream may have ended.")
                break

            frame_count += 1
            if frame_count % capture_rate != 0:
                continue

            # Optional rotation
            if rotate == 90:
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            elif rotate == 180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)

            # Encode as JPEG
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
            ok, buffer = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                log.warning("imencode failed on frame #%d", frame_count)
                continue

            frame_bytes = buffer.tobytes()
            if len(frame_bytes) > MAX_FRAME_BYTES:
                log.warning(
                    "Frame #%d too large (%d bytes) — dropping. Lower quality or resolution.",
                    frame_count, len(frame_bytes),
                )
                continue

            payload = json.dumps({
                "image_data":        base64.b64encode(frame_bytes).decode("utf-8"),
                "source":            str(source),
                "capture_timestamp": int(time.time() * 1000),
                "frame_number":      frame_count,
            })

            try:
                response = requests.post(
                    ingestion_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
                if response.status_code == 200:
                    sent_count += 1
                    log.info(
                        "  ↑ Frame #%d  size=%s KB  sent=%d  errors=%d",
                        frame_count,
                        f"{len(frame_bytes) / 1024:.1f}",
                        sent_count,
                        error_count,
                    )
                else:
                    error_count += 1
                    log.error("Ingestion failed: HTTP %d - %s", response.status_code, response.text)
            except Exception as exc:
                error_count += 1
                log.error("Network error: %s", exc)

    finally:
        cap.release()
        elapsed = time.time() - session_start
        log.info(
            "Session ended — frames captured=%d sent=%d errors=%d duration=%.0fs",
            frame_count, sent_count, error_count, elapsed,
        )


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VisionDetectAI — Video capture client (HTTP Ingestion)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source", default="0",
        help="Video source: '0' for default webcam, or an MJPEG/RTSP URL.",
    )
    parser.add_argument(
        "--url", required=True,
        help="Lambda Ingestion URL (from SAM outputs).",
    )
    parser.add_argument(
        "--rate", type=int, default=10,
        help="Send 1 frame every N frames (throttle).",
    )
    parser.add_argument(
        "--rotate", type=int, default=None,
        choices=[90, 180],
        help="Rotate each frame before sending.",
    )
    parser.add_argument(
        "--quality", type=int, default=JPEG_QUALITY,
        help="JPEG encoding quality (0-100).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # Coerce '0', '1', … to integers for OpenCV device index
    source: int | str = int(args.source) if args.source.isdigit() else args.source

    capture_and_send(
        source=source,
        ingestion_url=args.url,
        capture_rate=args.rate,
        rotate=args.rotate,
        jpeg_quality=args.quality,
    )
