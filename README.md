# VisionDetectAI

VisionDetectAI is a serverless, multi-platform object detection project built around Amazon Rekognition, AWS Lambda, S3, DynamoDB, API Gateway, and SNS. It supports real-time webcam/video-stream analysis and static image upload workflows, with a dashboard for viewing detections and alerts.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [API Endpoints](#api-endpoints)
- [Web Dashboard](#web-dashboard)
- [Mobile App](#mobile-app)
- [Deployment](#deployment)
- [Project Notes](#project-notes)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

The goal of VisionDetectAI is to make it easy to:

- capture frames from a live camera or video stream,
- upload still images for analysis,
- detect objects, text, and moderation signals with Amazon Rekognition,
- store and retrieve processed results efficiently,
- notify users when watch-list objects or unsafe content are detected.

The repository contains backend/serverless components, a web dashboard, a Python capture client, and a mobile app directory.

---

## Features

- Real-time video frame ingestion from webcam, MJPEG, RTSP, or video files
- Static image upload and analysis workflow
- Parallel Amazon Rekognition processing for labels, text, and moderation
- AWS Lambda-based serverless processing
- S3 storage for raw and processed image assets
- DynamoDB metadata storage for detections and frame history
- SNS notifications for alerting and watch-list detections
- Dashboard for browsing live and recent detection results
- Support for both web and Android/mobile usage paths

---

## Architecture

### Live Stream Pipeline

```text
Camera / Stream
    ↓
Python OpenCV Capture Client
    ↓ (Base64 over HTTP POST)
Lambda Function URL (ImageProcessor)
    ↓
S3 (raw frames)
    ↓
Amazon Rekognition (labels, text, moderation)
    ↓
DynamoDB (enriched frame metadata)
    ↓
API Gateway / FrameFetcher Lambda
    ↓
Web Dashboard

Amazon SNS is used for alerts when configured watch-list conditions are met.
```

### Static Upload Pipeline

```text
Browser / Android App
    ↓
API Gateway
    ↓
Presign Lambda
    ↓
S3 presigned PUT URL
    ↓
S3 (uploaded images)
    ↓
Detect Lambda
    ↓
Amazon Rekognition
    ↓
DynamoDB + annotated S3 output
```

---

## Repository Structure

Based on the current repository layout, the main areas are:

```text
VisionDetectAI/
├── README.md
├── client/
│   ├── video_capture.py
│   └── requirements.txt
├── frontend/
│   └── index.html
├── rn-app/
│   └── ...
├── scripts/
│   └── deploy.ps1
├── src/
│   ├── image_processor/
│   ├── frame_fetcher/
│   ├── detect/
│   └── ...
├── template.yaml
└── samconfig.toml
```

### Important components

- `client/video_capture.py` — local client that captures frames and sends them to the ingestion endpoint
- `src/image_processor/` — real-time frame processing Lambda
- `src/frame_fetcher/` — API for retrieving processed frame data for the dashboard
- `src/detect/` — static image detection Lambda
- `frontend/index.html` — web dashboard UI
- `rn-app/` — mobile app project
- `template.yaml` — AWS SAM / CloudFormation infrastructure definition
- `scripts/deploy.ps1` — deployment helper script

---

## Tech Stack

- **Python** — backend lambdas and capture client
- **HTML / JavaScript** — web dashboard and frontend UI
- **Kotlin** — mobile/Android-related code
- **PowerShell** — deployment automation
- **Shell** — supporting scripts

### AWS services

- Amazon Rekognition
- AWS Lambda
- Amazon S3
- Amazon DynamoDB
- Amazon API Gateway
- Amazon SNS
- AWS SAM / CloudFormation

---

## Getting Started

### Prerequisites

- AWS account with permissions to create Lambda, S3, DynamoDB, API Gateway, and SNS resources
- Python 3.x
- `pip`
- AWS CLI and AWS SAM installed
- Optional: Node.js/Expo or Android tooling if you want to work on the mobile app

### 1. Deploy the infrastructure

```powershell
./scripts/deploy.ps1 -NotificationEmail "your@email.com"
```

After deployment, save the output values such as the ingestion URL and any API endpoints.

### 2. Set up the capture client

```bash
cd client
pip install -r requirements.txt
```

### 3. Start streaming from a webcam

```bash
python video_capture.py --url "YOUR_LAMBDA_FUNCTION_URL" --source 0
```

### 4. Stream from a video file or MJPEG URL

```bash
python video_capture.py --url "YOUR_URL" --source "path/to/video.mp4" --rate 15
```

---

## Configuration

Typical setup items you may need to configure:

- AWS region
- S3 bucket names
- DynamoDB table names
- SNS notification email or phone number
- Rekognition thresholds / confidence values
- Ingestion URL for the live stream client

If you add or change environment variables in the Lambdas, document them in the related source folders as well.

---

## API Endpoints

| Endpoint | Method | Description |
|---|---:|---|
| `/presign` | `GET` | Returns a presigned URL for uploading a static image |
| `/results/{id}` | `GET` | Returns AI analysis for a static upload |
| `/enrichedframe` | `GET` | Returns recent processed frames for the dashboard |
| Lambda Function URL | `POST` | Receives raw frame data from the capture client |

---

## Web Dashboard

The dashboard is intended to provide a mission-control style interface for monitoring detections.

Expected dashboard capabilities:

- Live feed of processed frames
- Bounding boxes and overlays on detections
- Alert history for watch-list matches
- Confidence scores and text detection output

---

## Mobile App

The repository includes an `rn-app/` directory, which indicates a mobile app component is part of the project.

If the mobile app is intended for end users, this README should be expanded later with:

- app purpose
- setup instructions
- build and run commands
- platform-specific requirements
- API integration details

---

## Deployment

The repository appears to use AWS SAM / CloudFormation for deployment.

Useful files:

- `template.yaml` — infrastructure definition
- `samconfig.toml` — deployment configuration
- `scripts/deploy.ps1` — convenience deployment script

Recommended deployment documentation to include:

- required AWS credentials setup
- SAM build/deploy steps
- environment-specific configuration
- how to clean up resources

---

## Project Notes

- Temporary frames may be stored in S3 and automatically expired by lifecycle rules.
- DynamoDB indexing is used to make recent frame retrieval efficient.
- Rekognition results should be stored using data types compatible with DynamoDB.
- If alerts are enabled, SNS subscriptions must be confirmed before notifications will be delivered.

---

## Contributing

Contributions are welcome.

If you contribute, please keep documentation aligned with code changes and update this README when you add:

- new endpoints
- new detection modes
- new UI features
- new deployment steps
- new platform support

---

## License

Add the license information here if/when the project license is defined.
