#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/ma352-bridge}"
SERVICE_NAME="${SERVICE_NAME:-ma352-bridge}"
ENV_FILE="/etc/default/${SERVICE_NAME}"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "This installer must run as root. Try: sudo $0"
  exit 1
fi

echo "Installing to ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

# Copy project files (exclude venv/git/pyc).
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude ".git" --exclude ".venv" --exclude "__pycache__" --exclude "*.pyc" \
    "${SCRIPT_DIR}/" "${INSTALL_DIR}/"
else
  tar --exclude=".git" --exclude=".venv" --exclude="__pycache__" --exclude="*.pyc" \
    -C "${SCRIPT_DIR}" -cf - . | tar -C "${INSTALL_DIR}" -xf -
fi

echo "Setting up Python venv"
python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" >/dev/null

if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" <<'EOF'
BRIDGE_HOST=0.0.0.0
BRIDGE_PORT=5000
SERIAL_PORT=/dev/ttyUSB0
SERIAL_BAUD=115200
HOLD_INTERVAL=0.12
QUERY_INTERVAL=5.0
QUERY_ON_CONNECT=1
EOF
fi

cat > "${UNIT_FILE}" <<EOF
[Unit]
Description=MA-352 RS-232 Bridge Service
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/app.py
Restart=on-failure
RestartSec=2
EnvironmentFile=-${ENV_FILE}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null
systemctl restart "${SERVICE_NAME}"

echo "Service status:"
systemctl status "${SERVICE_NAME}" --no-pager
