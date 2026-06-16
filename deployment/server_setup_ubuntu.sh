#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root: sudo bash deployment/server_setup_ubuntu.sh"
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl gnupg ufw docker.io docker-compose-plugin

systemctl enable --now docker

ufw allow OpenSSH
ufw allow 80/tcp
ufw --force enable

echo "Docker version:"
docker --version
docker compose version
echo "Server setup complete."
