#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root: sudo bash deployment/server_setup_ubuntu.sh"
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl gnupg ufw docker.io docker-compose-plugin
  ufw allow OpenSSH
  ufw allow 80/tcp
  ufw --force enable
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y ca-certificates curl gnupg2 docker-ce docker-compose-plugin
  if command -v firewall-cmd >/dev/null 2>&1; then
    systemctl enable --now firewalld || true
    firewall-cmd --permanent --add-service=ssh || true
    firewall-cmd --permanent --add-service=http || true
    firewall-cmd --reload || true
  fi
elif command -v yum >/dev/null 2>&1; then
  yum install -y ca-certificates curl gnupg2 docker-ce docker-compose-plugin
else
  echo "Unsupported Linux distribution: apt-get, dnf, or yum is required."
  exit 1
fi

systemctl enable --now docker

echo "Docker version:"
docker --version
docker compose version
echo "Server setup complete."
