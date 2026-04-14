"""
Unit tests for the presign Lambda handler.
Run with: python -m pytest tests/ -v

Mocks boto3 so no AWS credentials are needed.
"""
import json
import unittest
from unittest.mock import MagicMock, patch
import os

# Patch environment variables before handler import
os.environ.setdefault("BUCKET_NAME", "test-bucket")
os.environ.setdefault("ENVIRONMENT", "test")

from src.presign.handler import lambda_handler


def _event(filename="photo.jpg", content_type="image/jpeg", method="GET"):
    return {
        "httpMethod": method,
        "queryStringParameters": {
            "filename": filename,
            "contentType": content_type,
        },
    }


class TestPresignHandler(unittest.TestCase):

    @patch("src.presign.handler.s3_client")
    def test_returns_200_with_valid_params(self, mock_s3):
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

        resp = lambda_handler(_event(), None)

        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertIn("uploadUrl", body)
        self.assertIn("imageKey", body)
        self.assertTrue(body["imageKey"].startswith("images/"))

    def test_returns_400_missing_filename(self):
        event = _event()
        event["queryStringParameters"]["filename"] = ""
        resp = lambda_handler(event, None)
        self.assertEqual(resp["statusCode"], 400)
        self.assertIn("filename", json.loads(resp["body"])["error"])

    def test_returns_400_missing_content_type(self):
        event = _event()
        event["queryStringParameters"]["contentType"] = ""
        resp = lambda_handler(event, None)
        self.assertEqual(resp["statusCode"], 400)

    def test_returns_400_invalid_content_type(self):
        resp = lambda_handler(_event(content_type="application/pdf"), None)
        self.assertEqual(resp["statusCode"], 400)
        self.assertIn("unsupported", json.loads(resp["body"])["error"].lower())

    def test_returns_200_options_preflight(self):
        event = _event(method="OPTIONS")
        resp = lambda_handler(event, None)
        self.assertEqual(resp["statusCode"], 200)

    @patch("src.presign.handler.s3_client")
    def test_image_key_has_uuid_segment(self, mock_s3):
        mock_s3.generate_presigned_url.return_value = "https://example.com"
        resp = lambda_handler(_event(), None)
        key: str = json.loads(resp["body"])["imageKey"]
        # key format: images/YYYY/MM/DD/<uuid>/filename
        parts = key.split("/")
        self.assertEqual(parts[0], "images")
        self.assertGreaterEqual(len(parts), 5)

    @patch("src.presign.handler.s3_client")
    def test_cors_headers_present(self, mock_s3):
        mock_s3.generate_presigned_url.return_value = "https://example.com"
        resp = lambda_handler(_event(), None)
        self.assertIn("Access-Control-Allow-Origin", resp["headers"])
        self.assertEqual(resp["headers"]["Access-Control-Allow-Origin"], "*")

    def test_sanitizes_dangerous_filename(self):
        # Ensure path traversal chars are stripped
        event = _event(filename="../../etc/passwd.jpg")
        with patch("src.presign.handler.s3_client") as mock_s3:
            mock_s3.generate_presigned_url.return_value = "https://example.com"
            resp = lambda_handler(event, None)
        key = json.loads(resp["body"])["imageKey"]
        self.assertNotIn("..", key)


if __name__ == "__main__":
    unittest.main()
