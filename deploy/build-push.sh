#!/bin/bash
# Rebuild the cv image and push to ECR. Run on a box with >=8GB RAM — the
# Next.js build OOMs on 2GB (its build workers ignore NODE_OPTIONS and fall
# back to Node's RAM-based default heap). Typical flow: temporarily upsize the
# runtime VM to t3.large, run this, then downsize back to t3.small (pull-only).
set -euo pipefail
REGION=us-east-1
ACCOUNT=405633560616
REG=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com
URI=$REG/cryptocurrency-cv

docker build -t cryptocurrency-cv:local "https://github.com/nirholas/cryptocurrency.cv.git#main"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REG"
docker tag cryptocurrency-cv:local "$URI:latest"
docker push "$URI:latest"
echo "pushed $URI:latest"
