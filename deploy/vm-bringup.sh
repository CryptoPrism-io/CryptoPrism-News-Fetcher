#!/bin/bash
# Bring up cv + Redis and the ingester timer on the VM. Idempotent-ish;
# safe to re-run after a `git pull`. Run as root (SSM runs as root).
set -x
REPO=/opt/cpio-news-fetcher

cd "$REPO/deploy/cv" || { echo "FATAL: deploy/cv missing (wrong branch?)"; exit 1; }
# Pull the prebuilt cv image from ECR (no build on the runtime box).
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 405633560616.dkr.ecr.us-east-1.amazonaws.com
docker compose pull || { echo "FATAL: ecr pull failed"; exit 1; }
docker compose up -d || { echo "FATAL: compose up failed"; exit 1; }

cp "$REPO/deploy/systemd/cv-ingester.service" /etc/systemd/system/
cp "$REPO/deploy/systemd/cv-ingester.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cv-ingester.timer

# wait for cv to serve, warm the Redis-backed feed cache, then one ingest.
# cv bot-blocks by User-Agent, so send a browser UA (+ Sec-Fetch-Site for full access).
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
for i in $(seq 1 40); do
  curl -fsS -A "$UA" -H "Sec-Fetch-Site: same-origin" http://127.0.0.1:3000/api/news?limit=1 >/dev/null 2>&1 && break
  sleep 15
done
for i in 1 2 3 4; do
  curl -s -A "$UA" -H "Sec-Fetch-Site: same-origin" "http://127.0.0.1:3000/api/news?limit=50" >/dev/null 2>&1
  sleep 3
done
systemctl start cv-ingester.service
touch /opt/cv-bringup.DONE
echo "=== bringup done $(date -u) ==="
