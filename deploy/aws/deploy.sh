#!/usr/bin/env bash
# Provisions one EC2 instance, ships this repo to it, and starts the
# docker-compose stack (deploy/docker-compose.yml) on it. Linux/no-FileNet
# path only -- see deploy/windows/ for the FileNet-native alternative,
# which needs a Windows Server AMI instead (this script doesn't cover
# that; adapt the AMI lookup and user-data below, or provision the VM by
# hand and follow deploy/windows/README instructions on it).
#
# Prerequisites: aws-cli configured (`aws configure`), an EC2 key pair
# already created in the target region, and deploy/.env filled in (see
# deploy/.env.production.example) -- this script refuses to run without it.
#
# Usage:
#   ./deploy.sh <key-pair-name> [region] [instance-type]
#   ./deploy.sh my-keypair us-east-1 t3.medium
#
# Idempotent: re-running reuses the same security group and, if
# state/instance-id is already present, the same instance (just re-syncs
# the repo and restarts the stack) instead of launching a second one.
set -euo pipefail

KEY_NAME="${1:?Usage: ./deploy.sh <key-pair-name> [region] [instance-type]}"
REGION="${2:-${AWS_REGION:-us-east-1}}"
INSTANCE_TYPE="${3:-t3.medium}"
SG_NAME="cecm-deploy-sg"
STATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.state"
INSTANCE_ID_FILE="$STATE_DIR/instance-id"
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Missing $ENV_FILE -- copy deploy/.env.production.example to deploy/.env and fill it in first." >&2
    exit 1
fi

mkdir -p "$STATE_DIR"
export AWS_PAGER=""

echo "==> Region: $REGION, instance type: $INSTANCE_TYPE, key pair: $KEY_NAME"

echo "==> Ensuring security group ($SG_NAME) exists..."
SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=group-name,Values=$SG_NAME" \
    --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "None")

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
    VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" --filters "Name=is-default,Values=true" \
        --query "Vpcs[0].VpcId" --output text)
    SG_ID=$(aws ec2 create-security-group --region "$REGION" \
        --group-name "$SG_NAME" --description "C-ECM standalone deployment" --vpc-id "$VPC_ID" \
        --query "GroupId" --output text)
    for PORT in 22 80 443; do
        aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
            --protocol tcp --port "$PORT" --cidr 0.0.0.0/0 >/dev/null
    done
    echo "    created $SG_ID (22/80/443 open to 0.0.0.0/0 -- narrow this to your own IP/VPN for anything beyond a quick eval)"
else
    echo "    reusing $SG_ID"
fi

echo "==> Looking up the latest Ubuntu 24.04 LTS AMI..."
AMI_ID=$(aws ssm get-parameter --region "$REGION" \
    --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
    --query "Parameter.Value" --output text)
echo "    $AMI_ID"

USER_DATA_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../bootstrap-linux.sh"

if [ -f "$INSTANCE_ID_FILE" ]; then
    INSTANCE_ID=$(cat "$INSTANCE_ID_FILE")
    STATE=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
        --query "Reservations[0].Instances[0].State.Name" --output text 2>/dev/null || echo "gone")
    if [ "$STATE" = "gone" ] || [ "$STATE" = "terminated" ]; then
        echo "==> Previous instance ($INSTANCE_ID) is gone -- launching a new one."
        rm -f "$INSTANCE_ID_FILE"
    fi
fi

if [ ! -f "$INSTANCE_ID_FILE" ]; then
    echo "==> Launching EC2 instance..."
    INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" \
        --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" \
        --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
        --user-data "file://$USER_DATA_FILE" \
        --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=cecm-standalone}]" \
        --query "Instances[0].InstanceId" --output text)
    echo "$INSTANCE_ID" > "$INSTANCE_ID_FILE"
    echo "    launched $INSTANCE_ID -- waiting for it to enter 'running' state..."
    aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
else
    echo "==> Reusing existing instance $INSTANCE_ID"
fi

PUBLIC_IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
    --query "Reservations[0].Instances[0].PublicIpAddress" --output text)
echo "==> Public IP: $PUBLIC_IP"

echo "==> Waiting for SSH to come up (this can take ~30-60s after 'running')..."
for i in $(seq 1 20); do
    if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -i "$KEY_NAME.pem" \
        "ubuntu@$PUBLIC_IP" "cloud-init status --wait" >/dev/null 2>&1; then
        break
    fi
    sleep 10
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
echo "==> Syncing the repo to the instance (excludes .venv/node_modules/.git/local data)..."
rsync -az --delete \
    --exclude ".venv" --exclude "node_modules" --exclude ".git" \
    --exclude "backend/data" --exclude "**/__pycache__" \
    -e "ssh -o StrictHostKeyChecking=accept-new -i $KEY_NAME.pem" \
    "$REPO_ROOT/" "ubuntu@$PUBLIC_IP:/home/ubuntu/filenet-drive/"

echo "==> Starting the stack on the instance..."
ssh -o StrictHostKeyChecking=accept-new -i "$KEY_NAME.pem" "ubuntu@$PUBLIC_IP" \
    "cd /home/ubuntu/filenet-drive/deploy && sudo docker compose up -d --build"

echo ""
echo "==> Done. App reachable at: http://$PUBLIC_IP/"
echo "    (put a real domain + TLS in front of this before real production use -- see deploy/README.md)"
