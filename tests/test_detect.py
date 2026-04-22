"""
Smoke tests — tests/test_detect.py
====================================
Tests the detect Lambda handler with fully mocked boto3 clients.
No AWS credentials or live AWS resources are needed.

run:
    python -m pytest tests/ -v
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# ── Environment patches (MUST happen before handler import) ──
os.environ["TABLE_NAME"]  = "DetectionResults-test"
os.environ["BUCKET_NAME"] = "test-bucket"
os.environ["SNS_TOPIC_ARN"] = "arn:aws:sns:us-east-1:000000000000:test"
os.environ["HIGH_CONFIDENCE_THRESHOLD"] = "70"
os.environ["TTL_DAYS"] = "30"

# ── boto3 must be importable; mock the entire module before handler loads ──
# This prevents real boto3 clients being created at import time.
import unittest.mock as _mock

_fake_rek   = _mock.MagicMock()
_fake_ddb   = _mock.MagicMock()
_fake_table = _mock.MagicMock()

# Wire the DynamoDB resource mock so that .Table() returns _fake_table
_fake_ddb.Table.return_value = _fake_table

# Pre-patch boto3 before the handler module is imported
with (
    patch("boto3.client", return_value=_fake_rek),
    patch("boto3.resource", return_value=_fake_ddb),
):
    import src.detect.handler as detect_handler  # noqa: E402

# ── Fake Rekognition responses ────────────────────────────────
FAKE_LABELS = [
    {"Name": "Dog",    "Confidence": 98.5, "Instances": [], "Parents": []},
    {"Name": "Animal", "Confidence": 97.0, "Instances": [], "Parents": []},
]
FAKE_TEXT = [
    {"DetectedText": "HELLO", "Type": "LINE", "Confidence": 99.1},
]
FAKE_MODERATION: list = []


# ── S3 event factory ──────────────────────────────────────────
def _s3_event(
    bucket: str = "test-bucket",
    key:    str = "images/2024/01/01/uuid/photo.jpg",
) -> dict:
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key},
                }
            }
        ]
    }


# ─────────────────────────────────────────────────────────────
# Test class
# ─────────────────────────────────────────────────────────────
class TestDetectHandler(unittest.TestCase):
    """
    Each test replaces the module-level `rekognition` and `table`
    objects inside detect_handler directly, then restores them.
    This is the most reliable approach when the handler creates clients
    at import time.
    """

    def setUp(self):
        # Fresh mocks per test
        self.mock_rekognition = MagicMock()
        self.mock_table       = MagicMock()

        self.mock_rekognition.detect_labels.return_value = {
            "Labels": FAKE_LABELS
        }
        self.mock_rekognition.detect_text.return_value = {
            "TextDetections": FAKE_TEXT
        }
        self.mock_rekognition.detect_moderation_labels.return_value = {
            "ModerationLabels": FAKE_MODERATION
        }

        self.mock_dynamodb    = MagicMock()
        self.mock_dynamodb.Table.return_value = self.mock_table

        # Swap the module-level singletons
        self._orig_rek   = detect_handler.rekognition
        self._orig_ddb   = detect_handler.dynamodb
        detect_handler.rekognition = self.mock_rekognition
        detect_handler.dynamodb    = self.mock_dynamodb

    def tearDown(self):
        # Restore originals so other tests are unaffected
        detect_handler.rekognition = self._orig_rek
        detect_handler.dynamodb    = self._orig_ddb

    # ── Happy path ────────────────────────────────────────────

    def test_handler_returns_processed_key(self):
        """Standard s3:ObjectCreated → returns imageKey and label count."""
        result = detect_handler.lambda_handler(_s3_event(), None)

        self.assertEqual(result["imageKey"], "images/2024/01/01/uuid/photo.jpg")
        self.assertEqual(result["label_count"], 2)

    def test_dynamodb_put_item_called_once(self):
        """Exactly one DynamoDB put_item per image record."""
        detect_handler.lambda_handler(_s3_event(), None)
        self.mock_table.put_item.assert_called_once()

    def test_dynamodb_item_schema(self):
        """
        Verify the DynamoDB item has all required fields with correct types.
        """
        detect_handler.lambda_handler(_s3_event(), None)

        item: dict = self.mock_table.put_item.call_args.kwargs["Item"]

        self.assertEqual(item["imageKey"], "images/2024/01/01/uuid/photo.jpg")
        self.assertEqual(item["status"],   "COMPLETE")
        self.assertEqual(item["statusPk"], "COMPLETE")
        self.assertIsInstance(item["timestamp"], int)
        self.assertIsInstance(item["ttl"],       int)
        
        # Test custom fields stored
        self.assertIsInstance(item["labels"], list)
        self.assertIsInstance(item["text_detections"], list)
        self.assertIsInstance(item["moderation_labels"], list)
        self.assertIn("processing_time_ms", item)
        self.assertEqual(item["label_count"], 2)

    def test_dynamodb_ttl_is_30_days(self):
        """TTL = timestamp + 30 * 86400 (within 5-second clock tolerance)."""
        before = int(time.time())
        detect_handler.lambda_handler(_s3_event(), None)
        after  = int(time.time())

        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertGreaterEqual(item["ttl"], before + 30 * 86_400)
        self.assertLessEqual(   item["ttl"], after  + 30 * 86_400)

    def test_all_three_rekognition_apis_called(self):
        """All three Rekognition APIs fire for each image."""
        detect_handler.lambda_handler(_s3_event(), None)
        self.mock_rekognition.detect_labels.assert_called_once()
        self.mock_rekognition.detect_text.assert_called_once()
        self.mock_rekognition.detect_moderation_labels.assert_called_once()

    def test_rekognition_receives_correct_s3_ref(self):
        """Rekognition image dict points at the correct bucket/key."""
        detect_handler.lambda_handler(_s3_event(), None)

        _, call_kwargs = self.mock_rekognition.detect_labels.call_args
        image = call_kwargs["Image"]
        self.assertEqual(image["S3Object"]["Bucket"], "test-bucket")
        self.assertEqual(image["S3Object"]["Name"],
                         "images/2024/01/01/uuid/photo.jpg")

    def test_high_confidence_alert_sent(self):
        """High confidence labels publish to SNS."""
        with patch.object(detect_handler, "sns") as mock_sns:
            detect_handler.lambda_handler(_s3_event(), None)
            mock_sns.publish.assert_called_once()
            
            call_kwargs = mock_sns.publish.call_args.kwargs
            self.assertEqual(call_kwargs["TopicArn"], "arn:aws:sns:us-east-1:000000000000:test")
            self.assertIn("High confidence objects detected", call_kwargs["Message"])

    # ── Edge cases ────────────────────────────────────────────

    def test_rekognition_failure_raises_exception(self):
        """
        If a Rekognition call throws, the handler logs and raises the error.
        """
        from botocore.exceptions import ClientError

        self.mock_rekognition.detect_labels.side_effect = ClientError(
            {"Error": {"Code": "InvalidImageException", "Message": "Bad image"}},
            "DetectLabels",
        )

        with self.assertRaises(ClientError):
            detect_handler.lambda_handler(_s3_event(), None)

    def test_empty_records_list_raises_index_error(self):
        """Empty Records list should raise IndexError based on strict array access."""
        with self.assertRaises(IndexError):
            detect_handler.lambda_handler({"Records": []}, None)


if __name__ == "__main__":
    unittest.main()
