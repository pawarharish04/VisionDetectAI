# VisionDetectAI — Serverless Real-Time Object Detection

A high-performance, production-quality AWS serverless architecture for real-time video stream analysis and static image processing using Amazon Rekognition.

---

## 🏗️ Architecture Overview

VisionDetectAI implements a dual-path pipeline for AI analysis:

### 1. Live Stream Pipeline (Real-Time)
```text
[Camera/Stream] ───► Video Capture Client (Python/OpenCV)
                             │
                             │ (Base64 over HTTP POST)
                             ▼
                    Lambda Function URL (ImageProcessor)
                             │
                             ├─► S3 (raw frames/)
                             ├─► Rekognition (Parallel Analysis) ────► SNS (Alerts)
                             └─► DynamoDB (EnrichedFrame Metadata)
                                          │
                                          ▼ (GSI Query)
Web Dashboard ◀───────── API Gateway ─── FrameFetcher (Lambda)
```

### 2. Static Upload Pipeline (Manual)
```text
Browser/Android ───► API Gateway ───► Presign Lambda
                             │              └─► Returns S3 PUT URL
                             ▼
                      S3 (images/) ───► Detect Lambda (Rekognition)
                                                └─► DynamoDB & Annotated S3
```

---

## ✨ Key Features

- **🚀 Real-Time Video Analysis**: Capture frames from any webcam or MJPEG/RTSP stream and process them in the cloud with sub-second latency.
- **🧠 Parallel Rekognition**: Invokes multiple AI models (Labels, Text, Moderation) in parallel using `ThreadPoolExecutor` for minimum overhead.
- **⚡ Serverless Scaling**: No servers to manage. Scales automatically from 1 frame per minute to thousands of frames per second.
- **📡 Lambda Function URLs**: Uses high-throughput, low-latency direct Lambda ingestion to bypass account-level Kinesis subscription limits.
- **🔥 Watch-List Alerting**: Instant SNS (Email/SMS) notifications when specific objects (e.g., Weapons, Fire, Hazards) are detected with high confidence.
- **📊 Optimized Data Access**: Uses DynamoDB Global Secondary Indexes (GSI) for millisecond retrieval of recent video frames in the dashboard.

---

## 📂 Project Structure

```text
objectDetection/
├── client/
│   ├── video_capture.py       # Live stream ingestion client
│   └── requirements.txt       # Local client dependencies (OpenCV, requests)
├── src/
│   ├── image_processor/       # Real-time frame analysis Lambda
│   ├── frame_fetcher/         # Dashboard live-feed API Lambda
│   ├── detect/                # Static image analysis Lambda
│   └── ...                    # Other backend microservices
├── frontend/
│   └── index.html             # Premium glassmorphism Web UI
├── template.yaml              # SAM / CloudFormation Infrastructure
└── samconfig.toml             # Deployment configurations
```

---

## 🚀 Quick Start (Live Stream)

### 1. Deploy the Infrastructure
```powershell
./scripts/deploy.ps1 -NotificationEmail "your@email.com"
```
*Note: Save the `IngestionUrl` output by the deployment.*

### 2. Setup the Capture Client
```bash
cd client
pip install -r requirements.txt
```

### 3. Start Streaming
To stream from your default webcam:
```bash
python video_capture.py --url "YOUR_LAMBDA_FUNCTION_URL" --source 0
```

To stream from a video file or MJPEG URL:
```bash
python video_capture.py --url "YOUR_URL" --source "path/to/video.mp4" --rate 15
```

---

## 💻 Dashboard
The dashboard provides a "Mission Control" interface:
- **Live Feed**: Real-time rendering of processed frames with bounding boxes.
- **Alert History**: Log of recent watch-list detections.
- **Analysis View**: Detailed confidence scores and text detection results for every frame.

---

## 🛠️ Infrastructure Decisions

- **Direct Ingestion**: Switched from Kinesis to Lambda Function URLs to provide an immediate "zero-setup" experience that avoids common AWS account stream limits.
- **Boto3 Type Safety**: All Rekognition float outputs are automatically converted to `Decimal` types before DynamoDB persistence to ensure robust data integrity.
- **S3 Lifecycles**: All temporary video frames are automatically expired after 24 hours to keep storage costs at near-zero.
- **GSI Indexing**: Implemented `processed-timestamp-index` to allow the dashboard to perform time-series queries without expensive full-table scans.

---

## 📝 API Reference

| Endpoint | Method | Description |
|---|---|---|
| `GET /presign` | `GET` | Get a secure URL to upload a static image. |
| `GET /results/{id}` | `GET` | Get AI analysis for a specific static upload. |
| `GET /enrichedframe` | `GET` | Fetch the latest processed video frames for the feed. |
| `Lambda Function URL` | `POST` | Ingest raw frame data (Base64). |

---

## 🤝 Contributing
Feel free to open issues or PRs for new Rekognition feature integrations (like Face Comparison or PPE detection)!
