"""
tests/test_frame_fetcher.py
================================
Unit tests for the FrameFetcher Lambda handler.
All AWS calls are fully mocked — no real credentials needed.

Run:
    python -m pytest tests/test_frame_fetcher.py -v
"""

from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import MagicMock, patch

# ── Env vars MUST be set before handler import ─────────────────
os.environ["S3_BUCKET"]           = "test-video-bucket"
os.environ["DDB_TABLE"]           = "DetectionResults-test"
os.environ["DDB_GSI_NAME"]        = "processed_timestamp-index"
os.environ["FETCH_HORIZON_HRS"]   = "24"
os.environ["FETCH_LIMIT"]         = "5"
os.environ["PRESIGNED_URL_EXPIRY"] = "1800"

# ── Patch boto3 before import ──────────────────────────────────
import unittest.mock as _mock

_fake_s3    = _mock.MagicMock()
_fake_ddb   = _mock.MagicMock()
_fake_table = _mock.MagicMock()
_fake_ddb.Table.return_value = _fake_table

def _client_factory(service, **kwargs):
    return _fake_s3

with (
    patch("boto3.client",   side_effect=_client_factory),
    patch("boto3.resource", return_value=_fake_ddb),
):
    import src.frame_fetcher.handler as ff_handler  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────
def _make_frame(idx: int, watch_hit: bool = False) -> dict:
    ts = int((time.time() - idx * 60) * 1000)   # idx minutes ago
    return {
        "image_key":            f"frames/{ts}_abc{idx}.jpg",
        "processed_timestamp":  ts,
        "source":               "webcam-0",
        "labels": [
            {"name": "Person", "confidence": "97.5", "instances": []},
        ],
        "text_detections":       [],
        "moderation_labels":     [],
        "watch_list_triggered":  ["Person"] if watch_hit else [],
        "ttl":                   int(time.time()) + 30 * 86_400,
    }

_PRESIGNED_URL = "https://s3.amazonaws.com/test-video-bucket/frames/abc.jpg?X-Auth=sig"

_GET_EVENT  = {"httpMethod": "GET",     "queryStringParameters": None}
_OPT_EVENT  = {"httpMethod": "OPTIONS", "queryStringParameters": None}


# ─────────────────────────────────────────────────────────────
class TestFrameFetcherHandler(unittest.TestCase):

    def setUp(self):
        self.mock_s3    = MagicMock()
        self.mock_ddb   = MagicMock()
        self.mock_table = MagicMock()
        self.mock_ddb.Table.return_value = self.mock_table

        self.mock_s3.generate_presigned_url.return_value = _PRESIGNED_URL

        # Return 3 frames from DynamoDB scan by default
        self.mock_table.scan.return_value = {
            "Items": [_make_frame(i) for i in range(3)]
        }

        # Swap module-level singletons
        self._orig_s3  = ff_handler.s3
        self._orig_ddb = ff_handler.dynamo
        ff_handler.s3     = self.mock_s3
        ff_handler.dynamo = self.mock_ddb

    def tearDown(self):
        ff_handler.s3     = self._orig_s3
        ff_handler.dynamo = self._orig_ddb

    # ── Happy path ────────────────────────────────────────────

    def test_returns_200_on_get(self):
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        self.assertEqual(result["statusCode"], 200)

    def test_body_is_valid_json_list(self):
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        body   = json.loads(result["body"])
        self.assertIsInstance(body, list)

    def test_returns_correct_number_of_frames(self):
        result  = ff_handler.lambda_handler(_GET_EVENT, None)
        frames  = json.loads(result["body"])
        self.assertEqual(len(frames), 3)

    def test_each_frame_has_presigned_url(self):
        """Every returned frame must have a presigned_url key."""
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        frames = json.loads(result["body"])
        for f in frames:
            self.assertIn("presigned_url", f)
            self.assertEqual(f["presigned_url"], _PRESIGNED_URL)

    def test_presigned_url_generated_per_frame(self):
        """generate_presigned_url called once per frame."""
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        self.assertEqual(self.mock_s3.generate_presigned_url.call_count, 3)

    def test_presigned_url_params(self):
        """Pre-signed URL is a GET, targets correct bucket and key."""
        result  = ff_handler.lambda_handler(_GET_EVENT, None)
        calls   = self.mock_s3.generate_presigned_url.call_args_list
        for c in calls:
            _, kwargs = c
            self.assertEqual(c.args[0], "get_object")
            self.assertEqual(kwargs["Params"]["Bucket"], "test-video-bucket")
            self.assertTrue(kwargs["Params"]["Key"].startswith("frames/"))
            self.assertEqual(kwargs["ExpiresIn"], 1800)

    def test_cors_headers_present(self):
        """CORS headers must appear in every non-error response."""
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        self.assertIn("Access-Control-Allow-Origin",  result["headers"])
        self.assertIn("Access-Control-Allow-Headers", result["headers"])

    def test_frames_sorted_newest_first(self):
        """Frames should be ordered newest → oldest (descending timestamp)."""
        # Create frames in reverse order to verify sorting
        out_of_order = [_make_frame(3), _make_frame(1), _make_frame(2)]
        self.mock_table.scan.return_value = {"Items": out_of_order}

        result = ff_handler.lambda_handler(_GET_EVENT, None)
        frames = json.loads(result["body"])

        timestamps = [int(f["processed_timestamp"]) for f in frames]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    # ── CORS pre-flight ───────────────────────────────────────

    def test_options_returns_200(self):
        result = ff_handler.lambda_handler(_OPT_EVENT, None)
        self.assertEqual(result["statusCode"], 200)

    def test_options_does_not_call_dynamodb(self):
        ff_handler.lambda_handler(_OPT_EVENT, None)
        self.mock_table.scan.assert_not_called()

    # ── FETCH_LIMIT cap ───────────────────────────────────────

    def test_fetch_limit_caps_output(self):
        """Result must never exceed FETCH_LIMIT (5) even if DDB returns more."""
        # Return 10 frames — limit is 5
        self.mock_table.scan.return_value = {
            "Items": [_make_frame(i) for i in range(10)]
        }
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        frames = json.loads(result["body"])
        self.assertLessEqual(len(frames), 5)

    # ── Empty result ──────────────────────────────────────────

    def test_empty_ddb_returns_empty_list(self):
        self.mock_table.scan.return_value = {"Items": []}
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        frames = json.loads(result["body"])
        self.assertEqual(frames, [])
        self.assertEqual(result["statusCode"], 200)

    def test_no_presigned_url_on_empty_result(self):
        """generate_presigned_url must NOT be called when there are no frames."""
        self.mock_table.scan.return_value = {"Items": []}
        ff_handler.lambda_handler(_GET_EVENT, None)
        self.mock_s3.generate_presigned_url.assert_not_called()

    # ── Presigned URL failure is non-fatal ────────────────────

    def test_presigned_url_failure_returns_empty_string(self):
        """If presigned URL generation fails, presigned_url should be empty string, not crash."""
        from botocore.exceptions import ClientError

        self.mock_s3.generate_presigned_url.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Denied"}},
            "GeneratePresignedUrl",
        )
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        self.assertEqual(result["statusCode"], 200)
        frames = json.loads(result["body"])
        for f in frames:
            self.assertEqual(f["presigned_url"], "")

    # ── DynamoDB failure → 500 ────────────────────────────────

    def test_dynamodb_failure_returns_500(self):
        """DynamoDB scan failure must return HTTP 500 with error body."""
        from botocore.exceptions import ClientError

        self.mock_table.scan.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Table not found"}},
            "Scan",
        )
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        self.assertEqual(result["statusCode"], 500)

        body = json.loads(result["body"])
        self.assertIn("error", body)

    # ── Watch-list frames included ────────────────────────────

    def test_watch_list_triggered_field_preserved(self):
        """watch_list_triggered field must pass through unchanged."""
        self.mock_table.scan.return_value = {
            "Items": [_make_frame(0, watch_hit=True)]
        }
        result = ff_handler.lambda_handler(_GET_EVENT, None)
        frames = json.loads(result["body"])
        self.assertEqual(frames[0]["watch_list_triggered"], ["Person"])

    # ── Frame without image_key ───────────────────────────────

    def test_frame_missing_image_key_gets_empty_url(self):
        """Frames missing image_key should have presigned_url='' without crashing."""
        bad_item = _make_frame(0)
        del bad_item["image_key"]
        self.mock_table.scan.return_value = {"Items": [bad_item]}

        result = ff_handler.lambda_handler(_GET_EVENT, None)
        self.assertEqual(result["statusCode"], 200)
        frames = json.loads(result["body"])
        self.assertEqual(frames[0]["presigned_url"], "")


if __name__ == "__main__":
    unittest.main()
