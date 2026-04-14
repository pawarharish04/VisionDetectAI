#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# deploy.sh  — Linux/macOS one-shot deploy script
# ──────────────────────────────────────────────────────────────
set -euo pipefail

STACK_NAME="${STACK_NAME:-object-detection-dev}"
BUCKET_NAME="${BUCKET_NAME:-}"          # MUST be set — globally unique
REGION="${REGION:-us-east-1}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
NOTIFICATION_EMAIL="${NOTIFICATION_EMAIL:-}"

# ── Colour helpers ────────────────────────────────────────────
step()  { echo -e "\n\033[36m▶ $*\033[0m"; }
ok()    { echo -e "  \033[32m✓ $*\033[0m"; }
warn()  { echo -e "  \033[33m⚠ $*\033[0m"; }
fatal() { echo -e "  \033[31m✗ $*\033[0m"; exit 1; }

# ── Pre-flight ────────────────────────────────────────────────
step "Pre-flight checks"
command -v sam  &>/dev/null || fatal "SAM CLI not found. Install: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
command -v aws  &>/dev/null || fatal "AWS CLI not found."

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ok "AWS Account: $ACCOUNT_ID"

[[ -z "$BUCKET_NAME" ]] && fatal "BUCKET_NAME env variable not set. Export it before running this script."

SAM_BUCKET="${SAM_BUCKET:-sam-deploy-artifacts-${ACCOUNT_ID}-${REGION}}"

# ── SAM artifacts bucket ──────────────────────────────────────
step "Ensuring SAM artifacts bucket: $SAM_BUCKET"
if ! aws s3api head-bucket --bucket "$SAM_BUCKET" 2>/dev/null; then
    if [[ "$REGION" == "us-east-1" ]]; then
        aws s3api create-bucket --bucket "$SAM_BUCKET" --region "$REGION"
    else
        aws s3api create-bucket --bucket "$SAM_BUCKET" --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION"
    fi
    ok "Created: $SAM_BUCKET"
else
    ok "Bucket exists."
fi

# ── Build ─────────────────────────────────────────────────────
step "sam validate"
sam validate --region "$REGION"

step "sam build"
sam build --region "$REGION"

# ── Deploy ────────────────────────────────────────────────────
step "sam deploy → $STACK_NAME"

OVERRIDES="BucketName=${BUCKET_NAME} Environment=${ENVIRONMENT}"
[[ -n "$NOTIFICATION_EMAIL" ]] && OVERRIDES+=" NotificationEmail=${NOTIFICATION_EMAIL}"

sam deploy \
    --stack-name        "$STACK_NAME" \
    --s3-bucket         "$SAM_BUCKET" \
    --region            "$REGION" \
    --capabilities      CAPABILITY_NAMED_IAM \
    --no-confirm-changeset \
    --parameter-overrides $OVERRIDES

# ── Outputs ───────────────────────────────────────────────────
step "Stack outputs"
aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region     "$REGION" \
    --query      "Stacks[0].Outputs" \
    --output     table

ok "Done! Use the PresignEndpoint URL to test."
