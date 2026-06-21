#!/bin/bash
# sync_images.sh
# Syncs captured images from the Raspberry Pi to local directory and deletes them from the Pi.

set -e

REMOTE_HOST="pi3"
REMOTE_DIR="~/squirrel_soaker/captures/"
LOCAL_DIR="./data/raw/"

echo "Syncing images from Raspberry Pi..."

# Check if local directory exists
mkdir -p "$LOCAL_DIR"

# Perform rsync sync, deleting successfully transferred source files from the Pi.
# We specify -e to pass ConnectTimeout so it fails quickly if the Pi is unreachable.
rsync -avz -e "ssh -o ConnectTimeout=5 -o BatchMode=yes" --remove-source-files "${REMOTE_HOST}:${REMOTE_DIR}" "$LOCAL_DIR"

echo "Sync completed! Images downloaded to $LOCAL_DIR"
