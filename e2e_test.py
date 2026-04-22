import argparse
import base64
import json
import ssl
import time
import urllib.request
import urllib.parse
from urllib.error import URLError, HTTPError
import os
import sys

def download_sample_image(path="sample.jpg"):
    """Download a test image if one doesn't exist."""
    if not os.path.exists(path):
        url = "https://images.unsplash.com/photo-1543466835-00a7907e9de1?q=80&w=600&auto=format&fit=crop"
        print(f"[+] Downloading test image (Dog) to {path}...")
        urllib.request.urlretrieve(url, path)
    return path

def http_request(url, method="GET", headers=None, data=None):
    if headers is None: headers = {}
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    
    # Ignore SSL for test simplicity
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            body = response.read()
            return response.status, body
    except HTTPError as e:
        return e.code, e.read()

def main():
    parser = argparse.ArgumentParser(description="End-to-end Object Detection Test")
    parser.add_argument("--api-url", required=True, help="API Gateway Base URL")
    args = parser.parse_args()

    api_url = args.api_url.rstrip("/")
    image_path = download_sample_image()
    file_size = os.path.getsize(image_path)

    # 1. Get Presign URL
    print("\n[1] Requesting presign PUT URL...")
    params = urllib.parse.urlencode({"filename": "sample.jpg", "contentType": "image/jpeg"})
    status, body = http_request(f"{api_url}/presign?{params}")
    
    if status != 200:
        print(f"[-] Presign failed! HTTP {status}: {body.decode()}")
        sys.exit(1)
        
    presign_data = json.loads(body)
    upload_url = presign_data["uploadUrl"]
    image_key = presign_data["imageKey"]
    print(f"  ✓ Got upload URL for Key: {image_key}")

    # 2. Upload to S3
    print("\n[2] Uploading image directly to S3...")
    start_time = time.time()
    
    with open(image_path, "rb") as f:
        file_bytes = f.read()

    status, body = http_request(
        upload_url, 
        method="PUT", 
        headers={"Content-Type": "image/jpeg", "Content-Length": str(file_size)},
        data=file_bytes
    )

    if status not in [200, 204]:
        print(f"[-] Upload failed! HTTP {status}: {body.decode()}")
        sys.exit(1)
        
    print(f"  ✓ Upload successful. ({os.path.getsize(image_path) / 1024:.1f} KB)")

    # 3. Poll for results
    print("\n[3] Polling results API until 'complete'...")
    encoded_key = urllib.parse.quote(image_key, safe="")
    result_url = f"{api_url}/results/{encoded_key}"
    
    data = None
    poll_count = 0
    poll_start = time.time()
    
    while poll_count < 20: # max 60s
        time.sleep(3)
        poll_count += 1
        
        status, body = http_request(result_url)
        if status == 404:
            print(f"  [Poll {poll_count}] DynamoDB says 404 Not Ready...")
            continue
            
        if status != 200:
            print(f"[-] Polling failed! HTTP {status}: {body.decode()}")
            sys.exit(1)
            
        data = json.loads(body)
        print(f"  [Poll {poll_count}] Status: {data.get('status')}")
        
        if data.get("status") == "COMPLETE":
            break
        elif data.get("status") == "FAILED":
            print(f"[-] Detection failed inside Lambda: {data.get('errorMessage')}")
            sys.exit(1)

    total_time = time.time() - start_time

    if not data or data.get("status") != "COMPLETE":
        print("[-] Timed out waiting for completion.")
        sys.exit(1)

    # 4. Confirm Animated Image Access
    annotated_url = data.get("annotatedUrl")
    print("\n[4] Validating annotated image is accessible...")
    status, _ = http_request(annotated_url, method="HEAD")
    if status == 200:
        print("  ✓ Annotated image validated! (HTTP 200)")
    else:
        print(f"  ⚠ Annotated URL returned HTTP {status}")

    # 5. Extract Details
    labels = data.get("labels", [])
    top_label = labels[0] if labels else {"Name": "None", "Confidence": 0}

    # 6. Final Summary
    print("\n==================================")
    print(" 🔥 DETECTION SUMMARY")
    print("==================================")
    print(f" Image Key:       {image_key}")
    print(f" Top Label:       {top_label['Name']} ({top_label['Confidence']:.1f}%)")
    print(f" End-to-End Time: {total_time:.2f} seconds")
    print(f" Annotated URL:   {annotated_url}")
    print("==================================\n")

if __name__ == "__main__":
    main()
