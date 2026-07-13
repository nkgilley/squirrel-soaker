#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-pi5}"
PI_APP_DIR="${PI_APP_DIR:-/home/pi/squirrel_soaker}"
DEVICE_API_TOKEN="${DEVICE_API_TOKEN:-$(sed -n 's/^DEVICE_API_TOKEN=//p' .env 2>/dev/null | tail -n 1)}"

if [[ -z "${DEVICE_API_TOKEN}" ]]; then
    echo "DEVICE_API_TOKEN must be set in the environment or .env" >&2
    exit 1
fi
if [[ ! "${DEVICE_API_TOKEN}" =~ ^[A-Za-z0-9_-]{32,}$ ]]; then
    echo "DEVICE_API_TOKEN must contain at least 32 URL-safe characters" >&2
    exit 1
fi

echo "Deploying Raspberry Pi services to ${PI_HOST}:${PI_APP_DIR}"

ssh "${PI_HOST}" "mkdir -p '${PI_APP_DIR}' '${PI_APP_DIR}/captures'"
scp capture.py trigger_server.py pi_benchmark.py camera_stream.py squirrel_safety.py "${PI_HOST}:${PI_APP_DIR}/"
scp squirrel-capture.service squirrel-trigger.service squirrel-stream.service "${PI_HOST}:/tmp/"

ssh "${PI_HOST}" "
    set -e
    umask 077
    printf '%s\n' 'DEVICE_API_TOKEN=${DEVICE_API_TOKEN}' > '${PI_APP_DIR}/device.env'
    sudo install -m 0644 /tmp/squirrel-capture.service /etc/systemd/system/squirrel-capture.service
    sudo install -m 0644 /tmp/squirrel-trigger.service /etc/systemd/system/squirrel-trigger.service
    sudo install -m 0644 /tmp/squirrel-stream.service /etc/systemd/system/squirrel-stream.service
    sudo systemctl daemon-reload
    sudo systemctl enable squirrel-trigger.service squirrel-capture.service
    sudo systemctl disable squirrel-stream.service >/dev/null 2>&1 || true
    sudo systemctl restart squirrel-trigger.service squirrel-capture.service
    sudo systemctl stop squirrel-stream.service >/dev/null 2>&1 || true
    systemctl --no-pager --full status squirrel-trigger.service squirrel-capture.service | sed -n '1,80p'
"

echo "Pi deploy complete."
