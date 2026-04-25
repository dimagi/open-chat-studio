#!/usr/bin/env bash
# One-time AWS setup for .github/workflows/dora-report.yml.
#
# Creates an S3 bucket and an IAM role assumable via GitHub OIDC from this repo's
# main branch. Idempotency: re-running will error on resources that already exist
# (create-bucket, create-role); the put-* steps overwrite existing config.
#
# Prereqs:
#   - AWS CLI configured against the account that should host the role/bucket
#   - The GitHub Actions OIDC provider (token.actions.githubusercontent.com)
#     already exists in the account. If not, the create-role step will fail with
#     "No OpenIDConnect provider found" — see README note at the bottom.
#
# Usage:
#   Edit the variables below, then: bash scripts/dora_aws_setup.sh

set -euo pipefail

# ── EDIT THESE ──────────────────────────────────────────────────────────────
ACCOUNT_ID=324037314878
# AWS account ID (same one that hosts deploy.yml's github_deploy role)
REGION=us-east-1               # bucket region
BUCKET=dimagi-ocs-dora         # must be globally unique
ROLE_NAME=dora-report-uploader
REPO=dimagi/open-chat-studio
# ────────────────────────────────────────────────────────────────────────────

TRUST_POLICY=$(mktemp)
S3_POLICY=$(mktemp)
trap 'rm -f "$TRUST_POLICY" "$S3_POLICY"' EXIT

echo "==> Creating bucket s3://$BUCKET in $REGION"
if [ "$REGION" = "us-east-1" ]; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
else
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=$REGION"
fi

echo "==> Setting bucket ownership controls (BucketOwnerPreferred — allows ACLs)"
aws s3api put-bucket-ownership-controls --bucket "$BUCKET" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerPreferred}]'

echo "==> Configuring Block Public Access (permit public ACLs, block public bucket policies)"
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "==> Setting CORS so the dimagi.github.io/dora dashboard can fetch the JSON"
aws s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration '{
  "CORSRules": [{
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET"],
    "AllowedHeaders": ["*"]
  }]
}'

echo "==> Writing trust policy (restricted to $REPO main branch)"
cat > "$TRUST_POLICY" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:${REPO}:ref:refs/heads/main"
      }
    }
  }]
}
EOF

echo "==> Creating IAM role $ROLE_NAME"
aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document "file://$TRUST_POLICY" \
  --description "Used by .github/workflows/dora-report.yml to publish DORA report to S3"

echo "==> Attaching inline S3 policy"
cat > "$S3_POLICY" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:PutObject", "s3:PutObjectAcl"],
    "Resource": [
      "arn:aws:s3:::${BUCKET}/dora.db",
      "arn:aws:s3:::${BUCKET}/dora-report.json"
    ]
  }]
}
EOF

aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name dora-s3-access \
  --policy-document "file://$S3_POLICY"

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)

cat <<EOF

==============================================================================
Done. Set these as GitHub repo Variables
(Settings → Secrets and variables → Actions → Variables):

  DORA_AWS_ROLE_ARN = $ROLE_ARN
  DORA_AWS_REGION   = $REGION
  DORA_S3_BUCKET    = $BUCKET

Dashboard URL after first run:
  https://dimagi.github.io/dora/?url=https://${BUCKET}.s3.${REGION}.amazonaws.com/dora-report.json

If create-role failed with "No OpenIDConnect provider found", run once:
  aws iam create-open-id-connect-provider \\
    --url https://token.actions.githubusercontent.com \\
    --client-id-list sts.amazonaws.com \\
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
==============================================================================
EOF
