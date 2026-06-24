#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-pi3}"
PI_APP_DIR="${PI_APP_DIR:-/home/pi/squirrel_soaker}"

echo "Deploying Raspberry Pi services to ${PI_HOST}:${PI_APP_DIR}"

ssh "${PI_HOST}" "mkdir -p '${PI_APP_DIR}'"
scp capture.py trigger_server.py camera_stream.py "${PI_HOST}:${PI_APP_DIR}/"
scp squirrel-capture.service squirrel-trigger.service squirrel-stream.service "${PI_HOST}:/tmp/"

ssh "${PI_HOST}" "
    set -e
    sudo mv /tmp/squirrel-capture.service /etc/systemd/system/squirrel-capture.service
    sudo mv /tmp/squirrel-trigger.service /etc/systemd/system/squirrel-trigger.service
    sudo mv /tmp/squirrel-stream.service /etc/systemd/system/squirrel-stream.service
    sudo systemctl daemon-reload
    sudo systemctl enable squirrel-trigger.service squirrel-capture.service
    sudo systemctl disable squirrel-stream.service >/dev/null 2>&1 || true
    sudo systemctl restart squirrel-trigger.service squirrel-capture.service
    sudo systemctl stop squirrel-stream.service >/dev/null 2>&1 || true
    systemctl --no-pager --full status squirrel-trigger.service squirrel-capture.service | sed -n '1,80p'
"

echo "Pi deploy complete."
