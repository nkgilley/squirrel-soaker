#!/usr/bin/env bash
# Install the Pi agents and rendered systemd services on the configured SSH host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PI_HOST="${PI_HOST:-pi5}"
PI_USER="${PI_USER:-$(ssh "${PI_HOST}" 'id -un')}"
PI_APP_DIR="${PI_APP_DIR:-/home/${PI_USER}/squirrel_soaker}"
DEVICE_API_TOKEN="${DEVICE_API_TOKEN:-$(sed -n 's/^DEVICE_API_TOKEN=//p' "${REPO_ROOT}/.env" 2>/dev/null | tail -n 1)}"
MAC_IP="${MAC_IP:-$(sed -n 's/^MAC_IP=//p' "${REPO_ROOT}/.env" 2>/dev/null | tail -n 1)}"

if [[ -z "${DEVICE_API_TOKEN}" ]]; then
    echo "DEVICE_API_TOKEN must be set in the environment or .env" >&2
    exit 1
fi
if [[ ! "${DEVICE_API_TOKEN}" =~ ^[A-Za-z0-9_-]{32,}$ ]]; then
    echo "DEVICE_API_TOKEN must contain at least 32 URL-safe characters" >&2
    exit 1
fi
if [[ -z "${MAC_IP}" ]]; then
    echo "MAC_IP must be set in the environment or .env" >&2
    exit 1
fi

echo "Deploying Raspberry Pi services to ${PI_HOST}:${PI_APP_DIR}"

ssh "${PI_HOST}" "mkdir -p '${PI_APP_DIR}' '${PI_APP_DIR}/captures' '${PI_APP_DIR}/squirrel_soaker'"
scp "${SCRIPT_DIR}/capture.py" "${SCRIPT_DIR}/trigger_server.py" "${SCRIPT_DIR}/pi_benchmark.py" "${PI_HOST}:${PI_APP_DIR}/"
scp "${REPO_ROOT}/squirrel_soaker/"*.py "${PI_HOST}:${PI_APP_DIR}/squirrel_soaker/"
scp "${SCRIPT_DIR}/systemd/squirrel-capture.service" "${SCRIPT_DIR}/systemd/squirrel-trigger.service" "${PI_HOST}:/tmp/"

ssh "${PI_HOST}" "
    set -e
    umask 077
    printf '%s\n' 'DEVICE_API_TOKEN=${DEVICE_API_TOKEN}' 'MAC_IP=${MAC_IP}' > '${PI_APP_DIR}/device.env'
    for service in squirrel-capture squirrel-trigger; do
        sed -e 's|@PI_USER@|${PI_USER}|g' -e 's|@PI_APP_DIR@|${PI_APP_DIR}|g' \"/tmp/\${service}.service\" > \"/tmp/\${service}.rendered.service\"
        sudo install -m 0644 \"/tmp/\${service}.rendered.service\" \"/etc/systemd/system/\${service}.service\"
    done
    sudo systemctl daemon-reload
    sudo systemctl enable squirrel-trigger.service squirrel-capture.service
    sudo systemctl disable squirrel-stream.service >/dev/null 2>&1 || true
    sudo systemctl restart squirrel-trigger.service squirrel-capture.service
    sudo systemctl stop squirrel-stream.service >/dev/null 2>&1 || true
    systemctl --no-pager --full status squirrel-trigger.service squirrel-capture.service | sed -n '1,80p'
"

echo "Pi deploy complete."
