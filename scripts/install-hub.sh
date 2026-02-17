#!/usr/bin/env bash
# install-hub.sh — Dead Drop Hub setup for kratos (Debian 12)
# Run as root or with sudo on a fresh Debian 12 VM.
#
# What this does:
#   1. Install Docker + Python 3.12
#   2. Clone dead-drop-teams from GitLab
#   3. Build the Docker image
#   4. Create runtime directories
#   5. Install systemd service for hub server
#   6. Configure firewall
#
# Usage: sudo bash install-hub.sh

set -euo pipefail

REPO_URL="https://git.lionsden.dev/jesse/dead-drop-teams.git"
INSTALL_DIR="/opt/dead-drop-teams"
DATA_DIR="/var/lib/dead-drop"
ROOMS_DIR="${DATA_DIR}/rooms"
ARCHIVE_DIR="${DATA_DIR}/archive"
HUB_PORT=9500
ROOM_PORT_MIN=9501
ROOM_PORT_MAX=10500
SUBNET="192.168.0.0/22"

echo "=== Dead Drop Hub Installer ==="
echo "Target: $(hostname) ($(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2))"
echo ""

# ── Step 1: Install Docker ──────────────────────────────────────────

echo "[1/6] Installing Docker..."
if command -v docker &>/dev/null; then
    echo "  Docker already installed: $(docker --version)"
else
    apt-get update -qq
    apt-get install -y -qq docker.io docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    echo "  Docker installed: $(docker --version)"
fi

# ── Step 2: Install Python 3.12 ─────────────────────────────────────

echo "[2/6] Installing Python 3.12..."
if python3.12 --version &>/dev/null 2>&1; then
    echo "  Python 3.12 already installed: $(python3.12 --version)"
else
    apt-get install -y -qq python3.12 python3.12-venv python3-pip
    echo "  Python installed: $(python3.12 --version)"
fi

# ── Step 3: Clone repo ──────────────────────────────────────────────

echo "[3/6] Cloning dead-drop-teams..."
if [ -d "${INSTALL_DIR}" ]; then
    echo "  ${INSTALL_DIR} exists — pulling latest..."
    cd "${INSTALL_DIR}" && git pull --ff-only
else
    git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

# Install Python package (for hub.py imports)
cd "${INSTALL_DIR}"
python3.12 -m pip install -e . --break-system-packages 2>/dev/null || \
    python3.12 -m pip install -e .

echo "  Package installed."

# ── Step 4: Build Docker image ──────────────────────────────────────

echo "[4/6] Building Docker image..."
cd "${INSTALL_DIR}"
docker build -t dead-drop-server:latest .
echo "  Image built: $(docker images dead-drop-server:latest --format '{{.Size}}')"

# ── Step 5: Create directories + systemd service ────────────────────

echo "[5/6] Setting up directories and systemd service..."
mkdir -p "${ROOMS_DIR}" "${ARCHIVE_DIR}"

# Write systemd service file
cat > /etc/systemd/system/dead-drop-hub.service << 'UNIT'
[Unit]
Description=Dead Drop Hub Server
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/dead-drop-teams
Environment=DD_HUB_DB=/var/lib/dead-drop/hub.db
Environment=DD_HUB_PORT=9500
Environment=DD_DATA_DIR=/var/lib/dead-drop/rooms
Environment=DD_ARCHIVE_DIR=/var/lib/dead-drop/archive
ExecStart=/usr/bin/python3.12 -m dead_drop.hub
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable dead-drop-hub.service
systemctl start dead-drop-hub.service

echo "  Service installed and started."
echo "  Hub DB: /var/lib/dead-drop/hub.db"
echo "  Hub port: ${HUB_PORT}"

# ── Step 6: Firewall ────────────────────────────────────────────────

echo "[6/6] Configuring firewall..."
if command -v ufw &>/dev/null; then
    ufw allow from ${SUBNET} to any port ${HUB_PORT}:${ROOM_PORT_MAX} proto tcp comment "Dead Drop Hub + rooms"
    echo "  ufw: allowed ${SUBNET} → ports ${HUB_PORT}-${ROOM_PORT_MAX}/tcp"
elif command -v iptables &>/dev/null; then
    iptables -A INPUT -s ${SUBNET} -p tcp --dport ${HUB_PORT}:${ROOM_PORT_MAX} -j ACCEPT
    # Persist rules
    if command -v netfilter-persistent &>/dev/null; then
        netfilter-persistent save
    fi
    echo "  iptables: allowed ${SUBNET} → ports ${HUB_PORT}-${ROOM_PORT_MAX}/tcp"
else
    echo "  WARNING: No firewall detected. Manually allow ports ${HUB_PORT}-${ROOM_PORT_MAX} from ${SUBNET}."
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Hub server:  http://$(hostname -I | awk '{print $1}'):${HUB_PORT}/mcp"
echo "Room ports:  ${ROOM_PORT_MIN}-${ROOM_PORT_MAX}"
echo "Data dir:    ${DATA_DIR}"
echo "Logs:        journalctl -u dead-drop-hub -f"
echo ""
echo "Next steps:"
echo "  1. Test: curl http://localhost:${HUB_PORT}/mcp"
echo "  2. Hub management: python3.12 -m dead_drop.hub"
echo "  3. Room containers auto-spawn on create_room()"
