#!/bin/bash
# Card Validation System Configuration Script
# Run this after installation to configure the system

set -e

echo "==================================="
echo "Card Validation System Configuration"
echo "==================================="

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "Please edit .env file with your real credentials"
    echo "nano .env"
    exit 1
fi

# Source environment variables
source .env

# Validate required environment variables
echo "Validating configuration..."

required_vars=(
    "IVR_PHONE_NUMBER"
    "CARD_NUMBER"
    "TWILIO_ACCOUNT_SID"
    "TWILIO_AUTH_TOKEN"
    "ELEVENLABS_API_KEY"
    "AMI_SECRET"
)

missing_vars=()
for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ] || [[ "${!var}" == *"your_"* ]] || [[ "${!var}" == *"YOUR_"* ]]; then
        missing_vars+=("$var")
    fi
done

if [ ${#missing_vars[@]} -gt 0 ]; then
    echo "ERROR: The following required variables are not configured:"
    printf '%s\n' "${missing_vars[@]}"
    echo "Please edit .env file and set these values"
    exit 1
fi

echo "✓ All required environment variables are configured"

# Configure Asterisk SIP trunk for Twilio
echo "Configuring Asterisk SIP trunk for Twilio..."
sed -i "s/TWILIO_ACCOUNT_SID/$TWILIO_ACCOUNT_SID/g" asterisk/pjsip.conf
sed -i "s/TWILIO_AUTH_TOKEN/$TWILIO_AUTH_TOKEN/g" asterisk/pjsip.conf
sed -i "s/YOUR_SERVER_PUBLIC_IP/$(curl -s ifconfig.me)/g" asterisk/pjsip.conf

# Copy Asterisk configuration
sudo cp asterisk/pjsip.conf /etc/asterisk/pjsip_custom.conf
sudo chown asterisk:asterisk /etc/asterisk/pjsip_custom.conf

# Reload Asterisk
echo "Reloading Asterisk configuration..."
sudo asterisk -rx "pjsip reload"
sudo asterisk -rx "dialplan reload"

# Test AMI connection
echo "Testing AMI connection..."
python3 -c "
from panoramisk import Manager
import asyncio

async def test():
    manager = Manager(
        host='$AMI_HOST',
        port=$AMI_PORT,
        username='$AMI_USERNAME',
        secret='$AMI_SECRET'
    )
    await manager.connect()
    print('✓ AMI connection successful')
    await manager.close()

asyncio.run(test())
"

echo "==================================="
echo "Configuration completed successfully!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. Run ./run.sh to start the system"
echo "2. Monitor logs: tail -f $LOG_DIR/card_validation.log"
