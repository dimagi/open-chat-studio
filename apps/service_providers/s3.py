"""Shared S3 client factory.

Builds a boto3 S3 client from the same ``AWS_S3_*`` settings that django-storages reads,
so the WhatsApp audio upload path and the media storage backends stay in sync — and both
work against any S3-compatible endpoint (MinIO, Cloudflare R2, Backblaze B2, etc.).
"""

from django.conf import settings


def get_s3_client():
    import boto3  # noqa: PLC0415 - TID253: heavy lib, slow startup
    from botocore.client import Config  # noqa: PLC0415 - lazy: used with boto3

    config_kwargs: dict = {"signature_version": "s3v4"}
    if settings.AWS_S3_ADDRESSING_STYLE:
        config_kwargs["s3"] = {"addressing_style": settings.AWS_S3_ADDRESSING_STYLE}

    client_kwargs = {
        "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
        "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
        "region_name": settings.AWS_S3_REGION,
        "config": Config(**config_kwargs),
    }
    # Only set endpoint_url for S3-compatible storage; omit it so boto3 uses the AWS default.
    if settings.AWS_S3_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = settings.AWS_S3_ENDPOINT_URL

    return boto3.client("s3", **client_kwargs)
