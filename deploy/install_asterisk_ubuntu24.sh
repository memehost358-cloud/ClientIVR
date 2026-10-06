#!/usr/bin/env bash
# ============================================================
# install_asterisk_ubuntu24.sh
# Bring a fresh Ubuntu 24.04 VM to the state this project needs.
# Run as user "ubuntu". Tested on Oracle Cloud.
# ============================================================
set -euo pipefail

echo "===== [1/6] Installing OS packages ====="
sudo apt-get update -y
sudo apt-get install -y \
  asterisk asterisk-core-sounds-en-wav \
  python3.12 python3.12-venv python3-pip \
  sox curl unzip git

echo "===== [2/6] Creating runtime directories ====="
sudo mkdir -p /var/spool/asterisk/monitor
sudo mkdir -p /var/lib/card-validation-system/results
sudo mkdir -p /var/log/card-validation-system
sudo chown -R asterisk:asterisk /var/spool/asterisk/monitor
sudo chmod 2775 /var/spool/asterisk/monitor
sudo chown -R ubuntu:ubuntu /var/lib/card-validation-system
sudo chown -R ubuntu:ubuntu /var/log/card-validation-system

echo "===== [3/6] Installing Asterisk configs ====="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sudo cp "$SCRIPT_DIR/asterisk/sip.conf"        /etc/asterisk/sip.conf
sudo cp "$SCRIPT_DIR/asterisk/manager.conf"    /etc/asterisk/manager.conf
sudo cp "$SCRIPT_DIR/asterisk/rtp.conf"        /etc/asterisk/rtp.conf
sudo cp "$SCRIPT_DIR/asterisk/extensions.conf" /etc/asterisk/extensions.conf
sudo cp "$SCRIPT_DIR/asterisk/logger.conf"     /etc/asterisk/logger.conf
sudo cp "$SCRIPT_DIR/asterisk/modules.conf"    /etc/asterisk/modules.conf
sudo chown root:asterisk /etc/asterisk/sip.conf /etc/asterisk/manager.conf /etc/asterisk/rtp.conf
sudo chmod 640 /etc/asterisk/sip.conf /etc/asterisk/manager.conf /etc/asterisk/rtp.conf
sudo chmod 644 /etc/asterisk/extensions.conf /etc/asterisk/logger.conf /etc/asterisk/modules.conf

echo "===== [4/6] Installing systemd override ====="
sudo mkdir -p /etc/systemd/system/asterisk.service.d
sudo cp "$SCRIPT_DIR/systemd/env.conf" /etc/systemd/system/asterisk.service.d/env.conf
sudo chmod 644 /etc/systemd/system/asterisk.service.d/env.conf

echo "===== [5/6] Installing sudoers rule ====="
sudo cp "$SCRIPT_DIR/sudoers/clientivr-asterisk" /etc/sudoers.d/clientivr-asterisk
sudo chmod 440 /etc/sudoers.d/clientivr-asterisk

echo "===== [6/6] Enabling and starting Asterisk ====="
sudo systemctl daemon-reload
sudo systemctl enable asterisk
sudo systemctl restart asterisk
sleep 3
sudo systemctl --no-pager -l status asterisk | head -10

echo
echo "===== DONE ====="
echo "Next: replace the placeholder secrets in /etc/asterisk/sip.conf and"
echo "      /etc/asterisk/manager.conf, then run 'sip reload' and 'manager reload'."
echo "Also create .env from deploy/.env.example."
