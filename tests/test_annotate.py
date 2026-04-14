"""
Smoke tests — tests/test_annotate.py
====================================
Tests the annotate Lambda handler with mocked boto3, verifying Pillow
image manipulation and DynamoDB stream processing.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# ── Environment patches (MUST happen before handler import) ──
os.environ["TABLE_NAME"]  = "DetectionResults-test"
os.environ["BUCKET_NAME"] = "test-bucket"
os.environ["AWS_REGION"]  = "test-region"

# ── Import handler with mocked boto3 ──
import unittest.mock as _mock
_fake_s3    = _mock.MagicMock()
_fake_ddb   = _mock.MagicMock()
_fake_table = _mock.MagicMock()
_fake_ddb.Table.return_value = _fake_table

with (
    patch("boto3.client", return_value=_fake_s3),
    patch("boto3.resource", return_value=_fake_ddb),
):
    import src.annotate.handler as annotate_handler

# Generate a tiny solid-color RGB JPEG image
from PIL import Image
_img = Image.new("RGB", (100, 100), color="blue")
_img_buf = io.BytesIO()
_img.save(_img_buf, format="JPEG")
FAKE_IMAGE_BYTES = _img_buf.getvalue()

FAKE_RESULT = {
    "labels": [
        {
            "Name": "Dog",
            "Confidence": 98.76,
            "Instances": [
                {
                    "BoundingBox": {
                        "Width": 0.5,
                        "Height": 0.5,
                        "Left": 0.2,
                        "Top": 0.2
                    }
                }
            ]
        }
    ]
}

def _ddb_stream_event(
    event_name: str = "INSERT",
    status: str = "complete",
    image_key: str = "images/2024/uuid/photo.jpg",
    has_boxes: bool = True
) -> dict:
    
    result_data = FAKE_RESULT if has_boxes else {"labels": [{"Name": "Sky", "Confidence": 99.0, "Instances": []}]}
    
    return {
        "Records": [
            {
                "eventName": event_name,
                "dynamodb": {
                    "NewImage": {
                        "imageKey": {"S": image_key},
                        "timestamp": {"N": "1700000000"},
                        "status": {"S": status},
                        "result": {"S": json.dumps(result_data)}
                    }
                }
            }
        ]
    }


class TestAnnotateHandler(unittest.TestCase):

    def setUp(self):
        self.mock_s3 = MagicMock()
        self.mock_table = MagicMock()

        self.mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=FAKE_IMAGE_BYTES)),
            "ContentType": "image/jpeg"
        }

        # Swap module-level singletons
        self._orig_s3 = annotate_handler.s3_client
        self._orig_table = annotate_handler.table
        annotate_handler.s3_client = self.mock_s3
        annotate_handler.table = self.mock_table

    def tearDown(self):
        annotate_handler.s3_client = self._orig_s3
        annotate_handler.table = self._orig_table

    def test_skips_non_insert_events(self):
        event = _ddb_stream_event(event_name="MODIFY")
        annotate_handler.lambda_handler(event, None)
        self.mock_s3.get_object.assert_not_called()

    def test_skips_incomplete_status(self):
        event = _ddb_stream_event(status="failed")
        annotate_handler.lambda_handler(event, None)
        self.mock_s3.get_object.assert_not_called()

    def test_skips_if_no_bounding_boxes(self):
        event = _ddb_stream_event(has_boxes=False)
        annotate_handler.lambda_handler(event, None)
        self.mock_s3.get_object.assert_not_called()

    def test_happy_path_annotates_and_updates(self):
        event = _ddb_stream_event()
        annotate_handler.lambda_handler(event, None)

        self.mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket", 
            Key="images/2024/uuid/photo.jpg"
        )
        
        self.mock_s3.put_object.assert_called_once()
        put_kwargs = self.mock_s3.put_object.call_args.kwargs
        self.assertEqual(put_kwargs["Bucket"], "test-bucket")
        self.assertEqual(put_kwargs["Key"], "results/2024/uuid/photo.jpg")
        self.assertEqual(put_kwargs["ContentType"], "image/jpeg")
        self.assertIsInstance(put_kwargs["Body"], bytes)

        self.mock_table.update_item.assert_called_once()
        update_kwargs = self.mock_table.update_item.call_args.kwargs
        self.assertEqual(update_kwargs["Key"]["imageKey"], "images/2024/uuid/photo.jpg")
        self.assertEqual(update_kwargs["ExpressionAttributeValues"][":ak"], "results/2024/uuid/photo.jpg")
        self.assertIn("https://test-bucket.s3.test-region.amazonaws.com/results/", update_kwargs["ExpressionAttributeValues"][":au"])

if __name__ == "__main__":
    unittest.main()
