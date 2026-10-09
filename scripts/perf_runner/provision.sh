#!/usr/bin/env bash
# Launch the pinned EC2 instance used for performance benchmarks.
#
# Usage:
#   AMI_ID=ami-... SUBNET_ID=subnet-... SECURITY_GROUP_ID=sg-... KEY_NAME=my-key \
#       scripts/perf_runner/provision.sh
#
# The instance type and AMI are pinned on purpose: results are only comparable
# when the hardware and OS image never change. Changing either one means
# starting a new baseline.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-c7i.xlarge}"
VOLUME_GB="${VOLUME_GB:-100}"
NAME="${NAME:-ocs-perf-runner}"

: "${AMI_ID:?Set AMI_ID to a specific Ubuntu 24.04 AMI (see docs/developer_guides/testing/perf_runner.md)}"
: "${SUBNET_ID:?Set SUBNET_ID}"
: "${SECURITY_GROUP_ID:?Set SECURITY_GROUP_ID (no inbound rules are required)}"
: "${KEY_NAME:?Set KEY_NAME}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

instance_id="$(aws ec2 run-instances \
    --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --subnet-id "$SUBNET_ID" \
    --security-group-ids "$SECURITY_GROUP_ID" \
    --key-name "$KEY_NAME" \
    --instance-initiated-shutdown-behavior stop \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=${VOLUME_GB},VolumeType=gp3,DeleteOnTermination=true}" \
    --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
    --tag-specifications \
        "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}},{Key=purpose,Value=perf-benchmarks}]" \
    --user-data "file://${here}/bootstrap.sh" \
    --query 'Instances[0].InstanceId' \
    --output text)"

echo "Launched ${instance_id} (${INSTANCE_TYPE}, ${AMI_ID}, ${REGION})"
echo "Wait for it with: aws ec2 wait instance-running --region ${REGION} --instance-ids ${instance_id}"
echo "Then register the GitHub runner as described in docs/developer_guides/testing/perf_runner.md"
