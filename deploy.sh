#!/usr/bin/env bash
# Build qr-manager locally and ship it to SidecarNAS with no registry/GitHub involved.
# Run this from your own machine, inside the qr-manager/ project folder.
set -euo pipefail

IMAGE_TAG="qr-manager:latest"
NAS_HOST="Sidecarprod@10.10.0.10"
NAS_DIR="/volume2/docker/qr-manager"
TAR_NAME="qr-manager.tar"

echo "==> Building image locally"
docker build -t "$IMAGE_TAG" .

echo "==> Saving image to tarball"
docker save "$IMAGE_TAG" -o "$TAR_NAME"

echo "==> Making sure ${NAS_DIR} exists on the NAS"
ssh "$NAS_HOST" "mkdir -p ${NAS_DIR}"

echo "==> Copying tarball to NAS"
scp "$TAR_NAME" "${NAS_HOST}:${NAS_DIR}/${TAR_NAME}"

echo "==> Loading image into Docker on the NAS (needs sudo — no docker group membership yet)"
ssh "$NAS_HOST" "sudo docker load -i ${NAS_DIR}/${TAR_NAME} && rm ${NAS_DIR}/${TAR_NAME}"

echo "==> Restarting the container so it picks up the new image"
ssh "$NAS_HOST" "sudo docker restart qr-manager || echo 'Container not running yet — deploy docker-compose.local.yml once via UGOS Project Manager first.'"

rm -f "$TAR_NAME"
echo "==> Done."
