from unittest.mock import patch

from django.test import override_settings

from apps.service_providers.s3 import get_s3_client


@override_settings(
    AWS_S3_ENDPOINT_URL="http://minio:9000",
    AWS_ACCESS_KEY_ID="test-key",
    AWS_SECRET_ACCESS_KEY="test-secret",
    AWS_S3_REGION="us-east-1",
    AWS_S3_ADDRESSING_STYLE="path",
)
def test_get_s3_client_passes_endpoint_url():
    """Building a client is local (no network); the endpoint must match the configured URL."""
    client = get_s3_client()

    assert client.meta.endpoint_url == "http://minio:9000"


@patch("botocore.client.Config")
@patch("boto3.client")
def test_get_s3_client_passes_addressing_style(mock_client, mock_config):
    with override_settings(
        AWS_S3_ENDPOINT_URL="http://minio:9000",
        AWS_ACCESS_KEY_ID="test-key",
        AWS_SECRET_ACCESS_KEY="test-secret",
        AWS_S3_REGION="us-east-1",
        AWS_S3_ADDRESSING_STYLE="path",
    ):
        get_s3_client()

    config_kwargs = mock_config.call_args.kwargs
    assert config_kwargs["s3"] == {"addressing_style": "path"}
    # s3v4 is the standard signature version; the factory pins it explicitly.
    assert config_kwargs["signature_version"] == "s3v4"
    client_kwargs = mock_client.call_args.kwargs
    assert client_kwargs["endpoint_url"] == "http://minio:9000"


@patch("botocore.client.Config")
@patch("boto3.client")
def test_get_s3_client_defaults_to_aws_when_unset(mock_client, mock_config):
    """Without any S3-compatible overrides, the client targets AWS and omits the endpoint."""
    with override_settings(
        AWS_ACCESS_KEY_ID="test-key",
        AWS_SECRET_ACCESS_KEY="test-secret",
        AWS_S3_REGION="us-east-1",
        AWS_S3_ENDPOINT_URL=None,
        AWS_S3_ADDRESSING_STYLE=None,
    ):
        get_s3_client()

    client_kwargs = mock_client.call_args.kwargs
    assert "endpoint_url" not in client_kwargs
    config_kwargs = mock_config.call_args.kwargs
    assert "s3" not in config_kwargs
    assert config_kwargs["signature_version"] == "s3v4"
