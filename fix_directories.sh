#!/bin/bash
# Fix directory creation typo
sudo mkdir -p /var/lib/card-validation-system/results
sudo mkdir -p /var/log/card-validation-system
sudo mkdir -p /var/spool/asterisk/recordings
sudo mkdir -p /etc/asterisk/extensions_custom.d

# Set permissions
sudo chown -R $USER:$USER /var/lib/card-validation-system
sudo chown -R $USER:$USER /var/log/card-validation-system
sudo chown -R asterisk:asterisk /var/spool/asterisk/recordings

echo "Directories created and permissions set successfully"
