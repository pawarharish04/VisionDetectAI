#!/usr/bin/env python3
"""
Test the /presign endpoint locally or against a deployed API.

Usage:
    # Against a deployed API
    python scripts/test_presign.py --url https://<api-id>.execute-api.us-east-1.amazonaws.com/dev

    # Then upload using the returned URL
    python scripts/test_presign.py --url <api-url> --upload ./test_image.jpg
"""

import argparse
import json
import sys

import requests


def get_presigned_url(api_url: str, filename: str, content_type: str) -> dict:
    endpoint = f"{api_url.rstrip('/')}/presign"
    params   = {"filename": filename, "contentType": content_type}

    print(f"\n→ GET {endpoint}")
    print(f"  params: {params}")

    resp = requests.get(endpoint, params=params, timeout=15)
    print(f"  status: {resp.status_code}")

    resp.raise_for_status()
    data = resp.json()
    print(f"  imageKey : {data['imageKey']}")
    print(f"  expiresIn: {data['expiresIn']}s")
    return data


def upload_file(upload_url: str, file_path: str, content_type: str) -> None:
    print(f"\n→ PUT {upload_url[:80]}...")
    with open(file_path, "rb") as fh:
        body = fh.read()

    resp = requests.put(
        upload_url,
        data=body,
        headers={"Content-Type": content_type},
        timeout=60,
    )
    print(f"  status: {resp.status_code}")
    if resp.status_code == 200:
        print("  ✓ Upload successful!")
    else:
        print(f"  ✗ Upload failed: {resp.text}")


def main():
    parser = argparse.ArgumentParser(description="Test presign endpoint")
    parser.add_argument("--url",          required=True,  help="API Gateway base URL")
    parser.add_argument("--filename",     default="test.jpg", help="Filename to presign")
    parser.add_argument("--content-type", default="image/jpeg", help="MIME type")
    parser.add_argument("--upload",       default=None,   help="Local file to upload via presigned URL")
    args = parser.parse_args()

    try:
        data = get_presigned_url(args.url, args.filename, args.content_type)
        print("\nFull response:")
        print(json.dumps(data, indent=2))

        if args.upload:
            upload_file(data["uploadUrl"], args.upload, args.content_type)

    except requests.HTTPError as exc:
        print(f"\n✗ HTTP error: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n✗ Unexpected error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
