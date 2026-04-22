# Serverless Object Detection Pipeline
A production-quality AWS serverless architecture for image analysis using Amazon Rekognition.

## Architecture Overview
```
Browser
  │
  │ GET /presign?filename=photo.jpg&contentType=image/jpeg
  ▼
API Gateway ──► PresignFunction (Lambda)
                  └─► Returns presigned S3 PUT URL
  │
  │ PUT <presigned URL>   (direct upload — Lambda never touches the binary)
  ▼
S3 images/YYYY/MM/DD/<uuid>/photo.jpg
  │
  │ s3:ObjectCreated event 
  ▼
detect-objects Lambda
  ├─► Rekognition.detect_labels()        ─┐
  ├─► Rekognition.detect_text()           ├─ parallel via ThreadPoolExecutor
  └─► Rekognition.detect_moderation()   ─┘
        │
        ├─► DynamoDB  (DetectionResults table, 30-day TTL)
        ├─► S3        (annotated image → results/<uuid>.jpg) 
        └─► SNS       (high-confidence alert email)          
```
## Known Architecture Decisions

- PPE detection runs in parallel with labels, text, and moderation so one Rekognition round-trip does not block the others. That keeps end-to-end latency lower for each uploaded image while still storing one combined result record in DynamoDB.
- DynamoDB Streams trigger annotation as a separate async step so detection and image drawing stay decoupled. That pattern makes the pipeline more resilient, keeps the first write lightweight, and allows annotation retries or future downstream consumers without changing the upload flow.
- Presigned URLs are used so the browser uploads directly to S3 and Lambda never handles raw image bytes. That reduces Lambda memory and execution time, avoids API Gateway payload limits for large files, and keeps the compute layer focused on orchestration instead of file transfer.

## Project Structure
```
objectDetection/
├── template.yaml              # SAM / CloudFormation — all AWS resources
├── samconfig.toml             # Local deploy config (gitignored)
├── .gitignore
│
├── src/
│   └── presign/
│       ├── handler.py         #  — Presigned URL Lambda
│       └── requirements.txt
│
├── scripts/
│   ├── deploy.ps1             # Windows deploy (PowerShell)
│   ├── deploy.sh              # Linux/macOS deploy (Bash)
│   └── test_presign.py        # Quick endpoint test
│
└── tests/
    └── test_presign.py        # Unit tests (pytest, no AWS needed)
```

## What's Built 

| Resource | Details |
| **S3 Bucket** | `images/` and `results/` prefixes, versioning on, lifecycle rules |
| **CORS** | `PUT` allowed from `*`, `Content-Type` and `Authorization` headers |
| **IAM Role** | S3, Rekognition, DynamoDB, SNS, CloudWatch permissions (least privilege) |
| **DynamoDB** | `DetectionResults` table, PAY_PER_REQUEST, 30-day TTL, Streams enabled |
| **SNS Topic** | `DetectionAlerts` — optional email subscription |
| **Presign Lambda** | Returns presigned `PUT` URL + `imageKey` for downstream polling |
| **API Gateway** | `GET /presign?filename=X&contentType=Y` |

## Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) — run `aws configure` first
- [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) — `sam --version`
- Python 3.12+

## Deploy (Windows)

```powershell
# 1. Set a globally unique bucket name
$env:BUCKET_NAME = "my-detection-bucket-abc123"

# 2. Deploy
.\scripts\deploy.ps1 -BucketName $env:BUCKET_NAME -NotificationEmail "you@example.com"
```

## Deploy (Linux / macOS)

```bash
export BUCKET_NAME="my-detection-bucket-abc123"
export NOTIFICATION_EMAIL="you@example.com"
bash scripts/deploy.sh
```

## Test the Presign Endpoint

```bash
# Get a presigned URL
python scripts/test_presign.py \
    --url https://<api-id>.execute-api.us-east-1.amazonaws.com/dev \
    --filename photo.jpg \
    --content-type image/jpeg

# Get a presigned URL AND upload a local file end-to-end
python scripts/test_presign.py \
    --url <api-url> \
    --upload ./my_photo.jpg
```

## Run Unit Tests (no AWS needed)

```bash
pip install pytest requests
python -m pytest tests/ -v
```

## API Reference

### `GET /presign`

| Parameter | Required | Example |
|---|---|---|
| `filename` | ✅ | `photo.jpg` |
| `contentType` | ✅ | `image/jpeg` |

**Response:**
```json
{
  "uploadUrl":     "https://s3.amazonaws.com/... (presigned PUT)",
  "imageKey":      "images/2024/01/15/<uuid>/photo.jpg",
  "getUrl":        "https://s3.amazonaws.com/... (presigned GET)",
  "bucket":        "my-detection-bucket",
  "expiresIn":     300,
  "maxFileSizeMb": 20,
  "instructions": {
    "method": "PUT",
    "headers": { "Content-Type": "image/jpeg" },
    "note": "PUT body = raw file bytes. Do NOT use multipart/form-data."
  }
}
```

**Browser upload pattern:**
```javascript
const { uploadUrl, imageKey } = await fetch(
  `/presign?filename=photo.jpg&contentType=image/jpeg`
).then(r => r.json());

await fetch(uploadUrl, {
  method: "PUT",
  headers: { "Content-Type": "image/jpeg" },
  body: file,                    // raw File object — no FormData
});

// Poll for results (or use WebSocket in 
const results = await fetch(`/results/${encodeURIComponent(imageKey)}`).then(r => r.json());
```


 Lambda | Details |
| `detect-objects` | S3 trigger → parallel Rekognition → DynamoDB |
| `annotate-image` | Pillow bounding boxes → `results/` in S3 |
| Frontend | HTML drag-and-drop with presigned upload + polling |
| Notifications | SNS email when confidence > 90% |
| Dead-letter queue | SQS DLQ, 3 retries |
| CloudWatch | Dashboard: invocations, p99 latency, error rate |

## Cost Estimate (light usage)

| Service | Free Tier | Cost After |
|---|---|---|
| Lambda | 1M req/mo free | ~$0.20 per 1M |
| Rekognition | 5K images/mo free | $0.001 per image |
| S3 | 5 GB free | ~$0.023/GB |
| DynamoDB | 25 GB free | ~$1.25/M writes |
| API Gateway | 1M req/mo free | $3.50 per 1M |
