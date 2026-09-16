# Card Validation System

Professional card validation system for card activation IVR systems. This system validates card activation status and security code functionality similar to major card company operations.

## Purpose

This system is designed for card companies to validate their own card activation IVR systems and ensure proper security controls are in place.

## Required Services

### 1. SIP Trunk Provider (Required)
**Recommended Provider**: Twilio
- **Why**: Reliable, easy setup, good documentation
- **Cost**: ~$0.013/minute for outbound calls
- **Setup**: https://www.twilio.com/console
- **Alternative**: SIPstation, SignalWire, Plivo

### 2. Transcription Service (Required)
**Recommended Provider**: ElevenLabs Scribe
- **Why**: Word-level timestamps needed for response analysis
- **Cost**: ~$0.30/hour of audio
- **Setup**: https://elevenlabs.io/app/speech-to-text
- **API Key**: Get from ElevenLabs dashboard

### 3. Hosting Provider (Required)
**Recommended Provider**: DigitalOcean
- **Why**: Cost-effective, reliable, easy Asterisk setup
- **Cost**: ~$20/month for 2GB RAM, 1 vCPU
- **OS**: Ubuntu 22.04 LTS
- **Alternative**: Linode, Hetzner, AWS Lightsail

## Environment Variables Required

Create a `.env` file in the project directory:

```bash
# Company Information
COMPANY_NAME=YourCardCompany
SYSTEM_NAME=CardActivationSystem
OPERATED_BY=CardOperationsTeam
CONTACT_EMAIL=operations@yourcompany.com

# IVR Configuration
IVR_PHONE_NUMBER=18003679617
CARD_NUMBER=YOUR_REAL_CARD_NUMBER

# SIP Trunk Configuration (Twilio)
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=your_twilio_number

# Transcription Configuration (ElevenLabs)
ELEVENLABS_API_KEY=your_elevenlabs_api_key

# Asterisk AMI Configuration
AMI_HOST=127.0.0.1
AMI_PORT=5038
AMI_USERNAME=cardvalidator
AMI_SECRET=your_strong_ami_secret

# System Configuration
MAX_DAILY_CALLS=100
RATE_LIMIT_CALLS_PER_HOUR=10
CALL_COOLDOWN_SECONDS=30

# Storage Configuration
RESULTS_DIR=/var/lib/card-validation-system/results
RECORDINGS_DIR=/var/spool/asterisk/recordings
STATE_FILE=/var/lib/card-validation-system/state.json
```

## Quick Start

### 1. Get Required Services

#### Twilio SIP Trunk
1. Sign up at https://www.twilio.com/console
2. Get Account SID and Auth Token
3. Purchase a phone number
4. Configure SIP trunking

#### ElevenLabs Transcription
1. Sign up at https://elevenlabs.io
2. Get API key from dashboard
3. Enable Scribe API access

#### DigitalOcean Server
1. Create Ubuntu 22.04 droplet (2GB RAM, 1 vCPU)
2. SSH into the server
3. Follow deployment instructions

### 2. Deploy to Server

```bash
# Upload files to server
scp -r card_validation_system/ root@your-server-ip:/opt/

# SSH into server
ssh root@your-server-ip

# Navigate to project
cd /opt/card_validation_system

# Install dependencies
./install.sh

# Configure system
./configure.sh

# Run system
./run.sh
```

### 3. Configure Environment Variables

```bash
# Copy environment template
cp .env.example .env

# Edit with your real values
nano .env

# Source environment variables
source .env
```

## System Architecture

```
┌─────────────────────────┐
│  Card Validation System │  Main Python Application
└──────────┬───────────────┘
           │ SIP Calls (Twilio)
           ▼
┌─────────────────────────┐
│   Asterisk PBX          │  Telephony Platform
│   Ubuntu Server         │  DigitalOcean
└──────────┬───────────────┘
           │ DTMF → Card/Security Codes
           ▼
┌─────────────────────────┐
│   Your IVR System       │  Card Activation System
│   18003679617          │  Your IVR Phone Number
└──────────┬───────────────┘
           │ Audio Recording
           ▼
┌─────────────────────────┐
│   ElevenLabs Scribe    │  Transcription Service
└──────────┬───────────────┘
           │ Transcript + Timestamps
           ▼
┌─────────────────────────┐
│   Response Analysis    │  Classification Engine
└──────────┬───────────────┘
           │ Validation Results
           ▼
┌─────────────────────────┐
│   Reports              │  Validation + Compliance
└─────────────────────────┘
```

## File Structure

```
card_validation_system/
├── README.md                      # This file
├── .env.example                   # Environment variables template
├── install.sh                     # Installation script
├── configure.sh                   # Configuration script
├── run.sh                         # Execution script
├── requirements.txt               # Python dependencies
├── main.py                        # Main application
├── card_validator.py             # Card validation logic
├── ivr_client.py                 # IVR interaction client
├── transcription.py               # Transcription handler
├── response_analyzer.py          # Response classification
├── report_generator.py            # Report generation
├── compliance_checker.py         # Compliance validation
├── config.py                      # Configuration management
└── asterisk/
    ├── extensions.conf            # Asterisk dialplan
    └── pjsip.conf                 # SIP configuration
```

## Cost Estimation

### Monthly Costs
- **DigitalOcean Server**: $20/month
- **Twilio SIP Trunk**: ~$10-20/month (usage-based)
- **ElevenLabs Transcription**: ~$5-10/month (usage-based)
- **Total**: ~$35-50/month

### Per Validation Costs
- **Twilio Calls**: ~$0.013/minute
- **Transcription**: ~$0.30/hour of audio
- **Typical validation**: ~$0.50-1.00 per card

## Security Requirements

- Keep `.env` file permissions at 600
- Never commit `.env` to version control
- Use strong AMI secrets
- Restrict AMI to localhost only
- Enable firewall rules for SIP/RTP
- Rotate credentials regularly

## Support

For issues or questions:
- Email: operations@yourcompany.com
- Documentation: See individual file headers
- Troubleshooting: Check logs in `/var/log/card-validation-system/`
