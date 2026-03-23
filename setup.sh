#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup.sh — One-shot provisioning script for Ubuntu 24.04
# Run as root:  sudo bash setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="/opt/ytbot"
BOT_USER="ytbot"
SERVICE_FILE="systemd/ytbot.service"

echo "════════════════════════════════════════"
echo "  YTBot — Setup Script"
echo "════════════════════════════════════════"

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/7] Installing system packages…"
apt-get update -y
apt-get install -y python3.12 python3.12-venv python3-pip ffmpeg git curl

# ── 2. Dedicated system user ──────────────────────────────────────────────────
echo "[2/7] Creating system user '${BOT_USER}'…"
if ! id "${BOT_USER}" &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home-dir "${INSTALL_DIR}" "${BOT_USER}"
    echo "  ✓ User created."
else
    echo "  ✓ User already exists."
fi

# ── 3. Copy project files ─────────────────────────────────────────────────────
echo "[3/7] Copying project to ${INSTALL_DIR}…"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rsync -a --exclude='.git' --exclude='.venv' --exclude='venv' \
    --exclude='data' --exclude='logs' --exclude='credentials' \
    "${SCRIPT_DIR}/" "${INSTALL_DIR}/"

# ── 4. Python virtual environment ─────────────────────────────────────────────
echo "[4/7] Creating Python virtual environment…"
python3.12 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip --quiet
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" --quiet
echo "  ✓ Dependencies installed."

# ── 5. Runtime directories ────────────────────────────────────────────────────
echo "[5/7] Creating runtime directories…"
mkdir -p "${INSTALL_DIR}/data/downloads"
mkdir -p "${INSTALL_DIR}/logs"
mkdir -p "${INSTALL_DIR}/credentials"

# ── 6. Permissions ────────────────────────────────────────────────────────────
echo "[6/7] Setting ownership…"
chown -R "${BOT_USER}:${BOT_USER}" "${INSTALL_DIR}"
chmod 750 "${INSTALL_DIR}/credentials"

# ── 7. systemd service ────────────────────────────────────────────────────────
echo "[7/7] Installing systemd service…"
cp "${INSTALL_DIR}/${SERVICE_FILE}" /etc/systemd/system/ytbot.service
systemctl daemon-reload
systemctl enable ytbot
echo "  ✓ Service installed and enabled."

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "  Setup complete!"
echo "════════════════════════════════════════"
echo ""
echo "Next steps:"
echo ""
echo "  1. Copy your Service Account JSON key:"
echo "     cp /path/to/service_account.json ${INSTALL_DIR}/credentials/service_account.json"
echo "     chown ${BOT_USER}:${BOT_USER} ${INSTALL_DIR}/credentials/service_account.json"
echo ""
echo "  2. Create your .env file:"
echo "     cp ${INSTALL_DIR}/.env.example ${INSTALL_DIR}/.env"
echo "     nano ${INSTALL_DIR}/.env"
echo "     chown ${BOT_USER}:${BOT_USER} ${INSTALL_DIR}/.env"
echo "     chmod 600 ${INSTALL_DIR}/.env"
echo ""
echo "  3. Share your Drive folder with the Service Account email"
echo "     (see README for the exact steps)"
echo ""
echo "  4. Start the bot:"
echo "     systemctl start ytbot"
echo "     journalctl -u ytbot -f"
echo ""
