# AWS Fargate Deployment

Dimagi maintains a dedicated deployment automation repository for running Open Chat Studio on AWS using ECS Fargate:

**[dimagi/ocs-deploy](https://github.com/dimagi/ocs-deploy)**

This repository contains Terraform / infrastructure-as-code and deployment tooling for a production-grade AWS deployment.

## Architecture

The AWS deployment runs the three OCS process types as separate ECS Fargate tasks, backed by managed AWS services:

| Component | AWS Service |
|-----------|-------------|
| Web (gunicorn) | ECS Fargate service behind an Application Load Balancer |
| Celery worker | ECS Fargate service |
| Celery beat | ECS Fargate task (single instance) |
| PostgreSQL | Amazon RDS for PostgreSQL (with pgvector) |
| Redis | Amazon ElastiCache for Redis |
| Media storage | Amazon S3 |
| Container registry | Amazon ECR |
| Secrets management | AWS Secrets Manager |
| TLS | AWS Certificate Manager + ALB |

## pgvector on RDS

pgvector is supported on Amazon RDS for PostgreSQL **15.2 and later**. Enable it after provisioning your database:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

For RDS PostgreSQL, pgvector 0.7.0+ is available from PostgreSQL 15.6 / 16.2 engine versions.

## Getting Started

See the [ocs-deploy README](https://github.com/dimagi/ocs-deploy) for:

- Infrastructure provisioning with Terraform
- ECR image build and push
- ECS service configuration
- Secrets and environment variable management
- Deployment and rollback procedures

## Key Configuration for AWS

When deploying on AWS Fargate, set the following in addition to the [base configuration](./configuration.md):

```bash
# Use ECS task IAM role for S3/SES access instead of explicit keys where possible
USE_S3_STORAGE=True
AWS_PUBLIC_STORAGE_BUCKET_NAME=your-public-bucket
AWS_PRIVATE_STORAGE_BUCKET_NAME=your-private-bucket
AWS_S3_REGION=us-east-1

# SES for email
DJANGO_EMAIL_BACKEND=anymail.backends.amazon_ses.EmailBackend
# Omit AWS_SES_* keys if using the task IAM role
AWS_SES_REGION=us-east-1

# Redis with TLS (ElastiCache)
REDIS_URL=rediss://your-cluster.cache.amazonaws.com:6379
REDIS_USE_TLS=True

# Structured logging for CloudWatch
ENABLE_JSON_LOGGING=True
```

## Health Check

The ALB health check should be configured to call `/status` with a token from `HEALTH_CHECK_TOKENS`. Example target group health check path:

```text
/status?token=your-health-check-token
```
