#!/usr/bin/env bash
# Provisions one Azure VM, ships this repo to it, and starts the
# docker-compose stack (deploy/docker-compose.yml) on it. Linux/no-FileNet
# path only -- see deploy/windows/ for the FileNet-native alternative
# (adapt the --image/os-type below to a Windows Server image instead, or
# provision the VM by hand and follow deploy/windows/README on it).
#
# Prerequisites: az-cli logged in (`az login`), an SSH key pair, and
# deploy/.env filled in (see deploy/.env.production.example) -- this
# script refuses to run without it.
#
# Usage:
#   ./deploy.sh <ssh-public-key-path> [location] [vm-size]
#   ./deploy.sh ~/.ssh/id_rsa.pub eastus Standard_B2s
#
# Idempotent: re-running reuses the same resource group/VM (just re-syncs
# the repo and restarts the stack) instead of creating a second one.
set -euo pipefail

SSH_KEY="${1:?Usage: ./deploy.sh <ssh-public-key-path> [location] [vm-size]}"
LOCATION="${2:-eastus}"
VM_SIZE="${3:-Standard_B2s}"
RG_NAME="cecm-deploy-rg"
VM_NAME="cecm-standalone"
ADMIN_USER="cecm"
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Missing $ENV_FILE -- copy deploy/.env.production.example to deploy/.env and fill it in first." >&2
    exit 1
fi
if [ ! -f "$SSH_KEY" ]; then
    echo "SSH public key not found at $SSH_KEY -- generate one with 'ssh-keygen -t ed25519' first." >&2
    exit 1
fi

echo "==> Location: $LOCATION, VM size: $VM_SIZE"

echo "==> Ensuring resource group ($RG_NAME) exists..."
az group create --name "$RG_NAME" --location "$LOCATION" --output none

BOOTSTRAP_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../bootstrap-linux.sh"

if az vm show --resource-group "$RG_NAME" --name "$VM_NAME" >/dev/null 2>&1; then
    echo "==> Reusing existing VM $VM_NAME"
else
    echo "==> Creating VM $VM_NAME (Ubuntu 24.04 LTS)..."
    az vm create \
        --resource-group "$RG_NAME" --name "$VM_NAME" \
        --image "Ubuntu2404" --size "$VM_SIZE" \
        --admin-username "$ADMIN_USER" --ssh-key-values "$SSH_KEY" \
        --custom-data "$BOOTSTRAP_FILE" \
        --public-ip-sku Standard \
        --os-disk-size-gb 30 \
        --output none

    echo "==> Opening ports 80/443..."
    az vm open-port --resource-group "$RG_NAME" --name "$VM_NAME" --port 80 --priority 900 --output none
    az vm open-port --resource-group "$RG_NAME" --name "$VM_NAME" --port 443 --priority 901 --output none
fi

PUBLIC_IP=$(az vm show -d --resource-group "$RG_NAME" --name "$VM_NAME" --query publicIps -o tsv)
echo "==> Public IP: $PUBLIC_IP"

SSH_PRIVATE_KEY="${SSH_KEY%.pub}"
echo "==> Waiting for cloud-init (Docker install) to finish on the VM..."
for i in $(seq 1 20); do
    if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -i "$SSH_PRIVATE_KEY" \
        "$ADMIN_USER@$PUBLIC_IP" "cloud-init status --wait" >/dev/null 2>&1; then
        break
    fi
    sleep 10
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
echo "==> Syncing the repo to the VM (excludes .venv/node_modules/.git/local data)..."
rsync -az --delete \
    --exclude ".venv" --exclude "node_modules" --exclude ".git" \
    --exclude "backend/data" --exclude "**/__pycache__" \
    -e "ssh -o StrictHostKeyChecking=accept-new -i $SSH_PRIVATE_KEY" \
    "$REPO_ROOT/" "$ADMIN_USER@$PUBLIC_IP:/home/$ADMIN_USER/filenet-drive/"

echo "==> Starting the stack on the VM..."
ssh -o StrictHostKeyChecking=accept-new -i "$SSH_PRIVATE_KEY" "$ADMIN_USER@$PUBLIC_IP" \
    "cd /home/$ADMIN_USER/filenet-drive/deploy && sudo docker compose up -d --build"

echo ""
echo "==> Done. App reachable at: http://$PUBLIC_IP/"
echo "    (put a real domain + TLS in front of this before real production use -- see deploy/README.md)"
