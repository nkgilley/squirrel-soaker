#!/bin/bash
# Syncs captured images from the Raspberry Pi to the local raw data directory.

set -e

REMOTE_HOST="pi3"
REMOTE_DIR="~/squirrel_soaker/captures/"
LOCAL_DIR="./data/raw/"

echo "Syncing images from Raspberry Pi..."
mkdir -p "$LOCAL_DIR"
rsync -avz -e "ssh -o ConnectTimeout=5 -o BatchMode=yes" --remove-source-files "${REMOTE_HOST}:${REMOTE_DIR}" "$LOCAL_DIR"
echo "Sync completed! Images downloaded to $LOCAL_DIR"
