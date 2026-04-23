"""
tests/test_image_processor.py
================================
Unit tests for the Kinesis-triggered ImageProcessor Lambda.
All AWS calls are fully mocked — no real credentials needed.

Run:
    python -m pytest tests/test_image_processor.py -v
"""

from __future__ import annotations

import base64
import json
import os
import time
import unittest
from unittest.mock import MagicMock, call, patch

# ── Env vars MUST be set before the handler module is imported ──
os.environ["S3_BUCKET"]             = "test-video-bucket"
os.environ["DDB_TABLE"]             = "DetectionResults-test"
os.environ["SNS_TOPIC_ARN"]         = "arn:aws:sns:us-east-1:000000000000:test-alerts"
os.environ["REKOG_MAX_LABELS"]      = "20"
os.environ["REKOG_MIN_CONF"]        = "50"
os.environ["LABEL_WATCH_LIST"]      = "Person,Car,Weapon"
os.environ["LABEL_WATCH_MIN_CONF"]  = "85"
os.environ["TTL_DAYS"]              = "30"

# ── Patch boto3 at module level before import ──────────────────
import unittest.mock as _mock

_fake_rekog = _mock.MagicMock()
_fake_s3    = _mock.MagicMock()
_fake_ddb   = _mock.MagicMock()
_fake_table = _mock.MagicMock()
_fake_sns   = _mock.MagicMock()

_fake_ddb.Table.return_value = _fake_table

# boto3.client is called 3 times: rekognition, s3, sns
_client_map = {
    "rekognition": _fake_rekog,
    "s3":          _fake_s3,
    "sns":         _fake_sns,
}

def _client_factory(service, **kwargs):
    return _client_map.get(service, _mock.MagicMock())

with (
    patch("boto3.client",   side_effect=_client_factory),
    patch("boto3.resource", return_value=_fake_ddb),
):
    import src.image_processor.handler as proc_handler  # noqa: E402


# ── Fake Rekognition responses ─────────────────────────────────
FAKE_LABELS = [
    {"Name": "Person", "Confidence": 97.5, "Instances": []},
    {"Name": "Car",    "Confidence": 91.2, "Instances": []},
    {"Name": "Tree",   "Confidence": 60.0, "Instances": []},
]

FAKE_LABELS_NO_WATCH = [
    {"Name": "Tree",   "Confidence": 88.0, "Instances": []},
    {"Name": "Cloud",  "Confidence": 70.0, "Instances": []},
]

FAKE_TEXT = [
    {"DetectedText": "STOP",  "Type": "LINE",  "Confidence": 99.0},
    {"DetectedText": "s",     "Type": "WORD",  "Confidence": 99.0},   # WORD — should be excluded
]

FAKE_MOD: list = []

# ── Tiny 1×1 JPEG bytes (valid JPEG magic bytes) ───────────────
_TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e\xfe\xd9"
)

def _kinesis_event(image_bytes=_TINY_JPEG, source="webcam-0"):
    """Build a Kinesis event record wrapping a frame payload."""
    payload = json.dumps({
        "image_data":        base64.b64encode(image_bytes).decode("utf-8"),
        "source":            source,
        "capture_timestamp": int(time.time() * 1000),
    })
    b64_data = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
    return {
        "Records": [
            {"kinesis": {"data": b64_data}}
        ]
    }


# ─────────────────────────────────────────────────────────────
class TestImageProcessorHandler(unittest.TestCase):
    """Tests for the image_processor Lambda handler."""

    def setUp(self):
        # Fresh mocks per test
        self.mock_rekog = MagicMock()
        self.mock_rekog.detect_labels.return_value      = {"Labels":           FAKE_LABELS}
        self.mock_rekog.detect_text.return_value        = {"TextDetections":   FAKE_TEXT}
        self.mock_rekog.detect_moderation_labels.return_value = {"ModerationLabels": FAKE_MOD}

        self.mock_s3    = MagicMock()
        self.mock_ddb   = MagicMock()
        self.mock_table = MagicMock()
        self.mock_sns   = MagicMock()
        self.mock_ddb.Table.return_value = self.mock_table

        # Swap module-level singletons
        self._orig = {
            "rekog":  proc_handler.rekog,
            "s3":     proc_handler.s3,
            "dynamo": proc_handler.dynamo,
            "sns":    proc_handler.sns,
        }
        proc_handler.rekog  = self.mock_rekog
        proc_handler.s3     = self.mock_s3
        proc_handler.dynamo = self.mock_ddb
        proc_handler.sns    = self.mock_sns

    def tearDown(self):
        proc_handler.rekog  = self._orig["rekog"]
        proc_handler.s3     = self._orig["s3"]
        proc_handler.dynamo = self._orig["dynamo"]
        proc_handler.sns    = self._orig["sns"]

    # ── Happy path ────────────────────────────────────────────

    def test_s3_upload_called_once(self):
        """Raw frame must be uploaded to S3 under frames/ prefix."""
        proc_handler.lambda_handler(_kinesis_event(), None)
        self.mock_s3.put_object.assert_called_once()
        kwargs = self.mock_s3.put_object.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "test-video-bucket")
        self.assertTrue(kwargs["Key"].startswith("frames/"))
        self.assertEqual(kwargs["ContentType"], "image/jpeg")

    def test_dynamodb_put_item_called_once(self):
        """Exactly one DynamoDB write per frame."""
        proc_handler.lambda_handler(_kinesis_event(), None)
        self.mock_table.put_item.assert_called_once()

    def test_dynamodb_item_schema(self):
        """DynamoDB item contains required fields with correct types."""
        proc_handler.lambda_handler(_kinesis_event(), None)
        item = self.mock_table.put_item.call_args.kwargs["Item"]

        self.assertTrue(item["image_key"].startswith("frames/"))
        self.assertIsInstance(item["processed_timestamp"], int)
        self.assertIsInstance(item["labels"],              list)
        self.assertIsInstance(item["text_detections"],     list)
        self.assertIsInstance(item["moderation_labels"],   list)
        self.assertIsInstance(item["watch_list_triggered"], list)
        self.assertIsInstance(item["ttl"],                 int)
        self.assertEqual(item["source"], "webcam-0")

    def test_text_only_line_type_stored(self):
        """Only LINE-type text detections should be stored (not WORD)."""
        proc_handler.lambda_handler(_kinesis_event(), None)
        item = self.mock_table.put_item.call_args.kwargs["Item"]
        # FAKE_TEXT has 1 LINE and 1 WORD; only LINE should survive
        self.assertEqual(item["text_detections"], ["STOP"])

    def test_ttl_is_30_days_from_now(self):
        """TTL must be ~30 days in the future."""
        before = int(time.time())
        proc_handler.lambda_handler(_kinesis_event(), None)
        after  = int(time.time())
        item   = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertGreaterEqual(item["ttl"], before + 30 * 86_400)
        self.assertLessEqual(   item["ttl"], after  + 30 * 86_400 + 2)

    def test_all_three_rekognition_apis_called(self):
        """detect_labels, detect_text, and detect_moderation_labels must all fire."""
        proc_handler.lambda_handler(_kinesis_event(), None)
        self.mock_rekog.detect_labels.assert_called_once()
        self.mock_rekog.detect_text.assert_called_once()
        self.mock_rekog.detect_moderation_labels.assert_called_once()

    def test_rekognition_receives_bytes(self):
        """Rekognition calls must pass image bytes (not S3 ref)."""
        proc_handler.lambda_handler(_kinesis_event(), None)
        kwargs = self.mock_rekog.detect_labels.call_args.kwargs
        self.assertIn("Bytes", kwargs["Image"])
        self.assertIsInstance(kwargs["Image"]["Bytes"], bytes)

    # ── Watch-list alert ──────────────────────────────────────

    def test_sns_published_when_watch_list_hit(self):
        """SNS alert sent when a watch-list object exceeds confidence threshold."""
        # Person (97.5%) and Car (91.2%) are both in watch list at ≥85% conf
        proc_handler.lambda_handler(_kinesis_event(), None)
        self.mock_sns.publish.assert_called_once()

        call_kwargs = self.mock_sns.publish.call_args.kwargs
        self.assertEqual(call_kwargs["TopicArn"], "arn:aws:sns:us-east-1:000000000000:test-alerts")
        self.assertIn("Watch-list", call_kwargs["Message"])

    def test_watch_list_triggered_field_populated(self):
        """watch_list_triggered in DynamoDB contains matched labels."""
        proc_handler.lambda_handler(_kinesis_event(), None)
        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertIn("Person", item["watch_list_triggered"])
        self.assertIn("Car",    item["watch_list_triggered"])
        # Tree is not in watch list
        self.assertNotIn("Tree", item["watch_list_triggered"])

    def test_sns_not_published_when_no_watch_list_hit(self):
        """No SNS alert when no watch-list label exceeds threshold."""
        self.mock_rekog.detect_labels.return_value = {"Labels": FAKE_LABELS_NO_WATCH}
        proc_handler.lambda_handler(_kinesis_event(), None)
        self.mock_sns.publish.assert_not_called()

    def test_watch_list_triggered_empty_when_no_hit(self):
        """watch_list_triggered is empty list when nothing matches."""
        self.mock_rekog.detect_labels.return_value = {"Labels": FAKE_LABELS_NO_WATCH}
        proc_handler.lambda_handler(_kinesis_event(), None)
        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["watch_list_triggered"], [])

    def test_watch_list_below_conf_not_triggered(self):
        """A watch-list label below LABEL_WATCH_MIN_CONF must NOT trigger SNS."""
        low_conf_labels = [{"Name": "Person", "Confidence": 60.0, "Instances": []}]
        self.mock_rekog.detect_labels.return_value = {"Labels": low_conf_labels}
        proc_handler.lambda_handler(_kinesis_event(), None)
        self.mock_sns.publish.assert_not_called()

    # ── Multi-record batch ────────────────────────────────────

    def test_multiple_records_processed(self):
        """Each Kinesis record triggers independent processing."""
        event = _kinesis_event()
        # Duplicate the record
        event["Records"].append(event["Records"][0])

        proc_handler.lambda_handler(event, None)

        self.assertEqual(self.mock_s3.put_object.call_count, 2)
        self.assertEqual(self.mock_table.put_item.call_count, 2)

    def test_bad_record_does_not_abort_batch(self):
        """A corrupt record must not prevent subsequent records from processing."""
        good_payload   = json.dumps({
            "image_data":        base64.b64encode(_TINY_JPEG).decode("utf-8"),
            "source":            "cam",
            "capture_timestamp": int(time.time() * 1000),
        })
        good_b64 = base64.b64encode(good_payload.encode()).decode()

        corrupt_b64 = base64.b64encode(b"NOT_JSON").decode()

        event = {
            "Records": [
                {"kinesis": {"data": corrupt_b64}},   # bad — json.loads will fail
                {"kinesis": {"data": good_b64}},      # good — must still be processed
            ]
        }

        # Should not raise; should process the good record
        proc_handler.lambda_handler(event, None)
        self.mock_table.put_item.assert_called_once()

    # ── S3 failure path ───────────────────────────────────────

    def test_s3_failure_suppresses_record_not_batch(self):
        """
        If S3 put_object fails the handler logs the error and moves on —
        it must NOT raise at the lambda_handler level (that would retry the
        entire batch).  DynamoDB must NOT be written for the failed frame.
        """
        from botocore.exceptions import ClientError

        self.mock_s3.put_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "Bucket not found"}},
            "PutObject",
        )
        # Should not raise — the handler absorbs per-record errors
        proc_handler.lambda_handler(_kinesis_event(), None)
        # DynamoDB must NOT be written because S3 failed first
        self.mock_table.put_item.assert_not_called()

    # ── SNS failure is non-fatal ──────────────────────────────

    def test_sns_failure_is_non_fatal(self):
        """SNS publish errors should be caught; DynamoDB write must still happen."""
        from botocore.exceptions import ClientError

        self.mock_sns.publish.side_effect = ClientError(
            {"Error": {"Code": "AuthorizationError", "Message": "Denied"}},
            "Publish",
        )

        # Should not raise
        proc_handler.lambda_handler(_kinesis_event(), None)
        # DynamoDB write still happened
        self.mock_table.put_item.assert_called_once()

    # ── Source field propagation ──────────────────────────────

    def test_source_field_stored_in_ddb(self):
        """Custom source strings propagate correctly into DynamoDB."""
        proc_handler.lambda_handler(_kinesis_event(source="rtsp://192.168.1.10/stream"), None)
        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["source"], "rtsp://192.168.1.10/stream")


if __name__ == "__main__":
    unittest.main()
