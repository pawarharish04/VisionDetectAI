#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy the Object Detection serverless stack to AWS via SAM.

.DESCRIPTION
    Validates the SAM template, builds the Lambda packages, and deploys
    the CloudFormation stack.  Works on Windows (PowerShell), macOS, and Linux.

.PARAMETER StackName
    CloudFormation stack name (default: object-detection-dev)

.PARAMETER BucketName
    Globally unique S3 bucket name you want to create (MUST CHANGE before first deploy)

.PARAMETER Region
    AWS region (default: us-east-1)

.PARAMETER Environment
    Deployment environment: dev | staging | prod (default: dev)

.PARAMETER NotificationEmail
    Optional email address for SNS detection alerts

.EXAMPLE
    .\scripts\deploy.ps1 -BucketName "my-unique-bucket-abc123" -NotificationEmail "you@example.com"
#>

param(
    [string]$StackName       = "object-detection-dev",
    [string]$BucketName      = "object-detection-img-982389018373",
    [string]$Region          = "us-east-1",
    [string]$Environment     = "dev",
    [string]$SamBucket       = "",       # SAM deployment artifacts bucket (auto-created if empty)
    [string]$NotificationEmail = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Colour helpers ────────────────────────────────────────────
function Write-Step  { param($m) Write-Host "`n>>> $m" -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host "  [+] $m" -ForegroundColor Green }
function Write-Warn  { param($m) Write-Host "  [!] $m" -ForegroundColor Yellow }
function Write-Fatal { param($m) Write-Host "  [-] $m" -ForegroundColor Red; exit 1 }

# ── Pre-flight checks ─────────────────────────────────────────
Write-Step "Pre-flight checks"

if (-not (Get-Command sam -ErrorAction SilentlyContinue)) {
    Write-Fatal "AWS SAM CLI not found. Install from: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
}

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Write-Fatal "AWS CLI not found. Install from: https://aws.amazon.com/cli/"
}

# Check AWS credentials are configured
try {
    $identity = aws sts get-caller-identity --output json 2>&1 | ConvertFrom-Json
    Write-Ok "AWS identity: $($identity.Arn)"
} catch {
    Write-Fatal "AWS credentials not configured. Run: aws configure"
}

if ($BucketName -match "CHANGEME") {
    Write-Fatal "BucketName still has placeholder. Pass -BucketName 'your-unique-name'."
}

# ── SAM artifacts bucket ──────────────────────────────────────
if ($SamBucket -eq "") {
    $accountId  = $identity.Account
    $SamBucket  = "sam-deploy-artifacts-${accountId}-${Region}"
    Write-Warn "SAM artifacts bucket not set. Using: $SamBucket"
}

# Create SAM artifacts bucket if it doesn't exist
Write-Step "Ensuring SAM artifacts bucket exists: $SamBucket"
$ErrorActionPreference = "Continue"
$bucketExists = aws s3api head-bucket --bucket $SamBucket 2>&1
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) {
    if ($Region -eq "us-east-1") {
        aws s3api create-bucket --bucket $SamBucket --region $Region | Out-Null
    } else {
        aws s3api create-bucket `
            --bucket $SamBucket `
            --region $Region `
            --create-bucket-configuration LocationConstraint=$Region | Out-Null
    }
    Write-Ok "Created SAM artifacts bucket: $SamBucket"
} else {
    Write-Ok "SAM artifacts bucket already exists."
}

# ── SAM Validate ──────────────────────────────────────────────
Write-Step "Validating SAM template"
sam validate --region $Region
if ($LASTEXITCODE -ne 0) { Write-Fatal "SAM template validation failed." }
Write-Ok "Template is valid."

# ── SAM Build ─────────────────────────────────────────────────
Write-Step "Building Lambda packages (sam build)"
sam build --region $Region
if ($LASTEXITCODE -ne 0) { Write-Fatal "SAM build failed." }
Write-Ok "Build complete."

# ── SAM Deploy ────────────────────────────────────────────────
Write-Step "Deploying stack: $StackName"

$params  = @(
    "--stack-name",   $StackName,
    "--s3-bucket",    $SamBucket,
    "--region",       $Region,
    "--capabilities", "CAPABILITY_NAMED_IAM",
    "--no-confirm-changeset",
    "--parameter-overrides",
    "BucketName=$BucketName",
    "Environment=$Environment"
)

if ($NotificationEmail -ne "") {
    $params += "NotificationEmail=$NotificationEmail"
}

sam deploy @params
if ($LASTEXITCODE -ne 0) { Write-Fatal "SAM deploy failed." }

# ── Print outputs ─────────────────────────────────────────────
Write-Step "Stack outputs"
aws cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --query "Stacks[0].Outputs" `
    --output table

Write-Ok "Deployment complete! Use the PresignEndpoint URL to test."
