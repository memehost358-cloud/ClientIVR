#!/bin/bash
# Card Validation System Installation Script
# Run this on your Ubuntu server to install all dependencies

set -e

echo "==================================="
echo "Card Validation System Installation"
echo "==================================="

# Update system
echo "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install Python and dependencies
echo "Installing Python and dependencies..."
sudo apt install -y python3 python3-pip python3-venv build-essential

# Install Asterisk
echo "Installing Asterisk..."
sudo apt install -y asterisk asterisk-dahdi asterisk-extra-sounds

# Install additional dependencies
echo "Installing additional dependencies..."
sudo apt install -y git curl wget

# Create application directory
echo "Creating application directories..."
sudo mkdir -p /var/lib/card-validation-system
sudo mkdir -p /var/log/card-validation-system
sudo mkdir -p /var/spool/asterisk/recordings

# Set permissions
echo "Setting permissions..."
sudo chown -R $USER:$USER /var/lib/card-validation-system
sudo chown -R asterisk:asterisk /var/spool/asterisk/recordings
sudo chown -R $USER:$USER /var/log/card-validation-system

# Create Python virtual environment
echo "Creating Python virtual environment..."
python3 -m venv /var/lib/card-validation-system/venv

# Install Python dependencies
echo "Installing Python dependencies..."
source /var/lib/card-validation-system/venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Configure Asterisk
echo "Configuring Asterisk..."
sudo cp asterisk/extensions.conf /etc/asterisk/extensions_custom.d/
sudo chown asterisk:asterisk /etc/asterisk/extensions_custom.d/extensions.conf

# Include extensions in main config
if ! grep -q "extensions_custom.d/extensions.conf" /etc/asterisk/extensions_custom.conf; then
    echo '#include "extensions_custom.d/extensions.conf"' | sudo tee -a /etc/asterisk/extensions_custom.conf
fi

# Reload Asterisk
echo "Reloading Asterisk dialplan..."
sudo asterisk -rx "dialplan reload"

# Create AMI user
echo "Creating AMI user..."
sudo bash -c 'cat > /etc/asterisk/manager_custom.conf << EOF
[cardvalidator]
secret = CHANGE_THIS_SECRET_NOW
deny = 0.0.0.0/0
permit = 127.0.0.1/32
read = call,user,system
write = call,user,system,originate
EOF'

# Reload AMI
echo "Reloading AMI configuration..."
sudo asterisk -rx "manager reload"

# Configure firewall
echo "Configuring firewall..."
sudo ufw allow 22/tcp
sudo ufw allow from 127.0.0.1 to any port 5038 proto tcp
sudo ufw allow 5060/udp
sudo ufw allow 10000:20000/udp
sudo ufw --force enable

echo "==================================="
echo "Installation completed successfully!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. Run ./configure.sh to configure the system"
echo "2. Set your AMI secret in /etc/asterisk/manager_custom.conf"
echo "3. Add your Twilio credentials to .env file"
echo "4. Run ./run.sh to start the system"
