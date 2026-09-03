#!/bin/bash
# Cloud-init/user-data bootstrap for a fresh Ubuntu VM: installs Docker +
# the Compose plugin so deploy/aws/deploy.sh and deploy/azure/deploy.sh can
# rsync the repo up and run `docker compose up` against it. Nothing app-
# specific lives here -- both scripts pass this same file as the
# instance's startup script.
set -euo pipefail

apt-get update
apt-get install -y ca-certificates curl gnupg rsync

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

usermod -aG docker ubuntu || true
systemctl enable --now docker
