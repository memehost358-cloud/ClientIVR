# Card Validation System - Complete Deployment Guide

## 🎯 **OVERVIEW**

This is a **professional card validation system** for card companies to validate their card activation IVR systems. It works like Visa/MasterCard internal validation systems.

## 📋 **REQUIRED SERVICES**

### 1. **SIP Trunk Provider** (Required)
**Provider**: Twilio
- **Why**: Reliable, easy setup, good documentation
- **Cost**: ~$0.013/minute for outbound calls
- **Setup**: https://www.twilio.com/console
- **Required Credentials**:
  - Account SID
  - Auth Token
  - Phone Number

### 2. **Transcription Service** (Required)
**Provider**: ElevenLabs Scribe
- **Why**: Word-level timestamps needed for response analysis
- **Cost**: ~$0.30/hour of audio
- **Setup**: https://elevenlabs.io/app/speech-to-text
- **Required Credentials**:
  - API Key

### 3. **Hosting Provider** (Required)
**Provider**: DigitalOcean
- **Why**: Cost-effective, reliable, easy Asterisk setup
- **Cost**: ~$20/month for 2GB RAM, 1 vCPU
- **OS**: Ubuntu 22.04 LTS
- **Requirements**: Public IP, SSH access

## 🔧 **ENVIRONMENT VARIABLES REQUIRED**

Create a `.env` file with these exact variables:

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
TWILIO_ACCOUNT_SID=your_actual_account_sid
TWILIO_AUTH_TOKEN=your_actual_auth_token
TWILIO_PHONE_NUMBER=your_actual_twilio_number

# Transcription Configuration (ElevenLabs)
ELEVENLABS_API_KEY=your_actual_api_key

# Asterisk AMI Configuration
AMI_HOST=127.0.0.1
AMI_PORT=5038
AMI_USERNAME=cardvalidator
AMI_SECRET=your_strong_secret_here

# System Configuration
MAX_DAILY_CALLS=100
RATE_LIMIT_CALLS_PER_HOUR=10
CALL_COOLDOWN_SECONDS=30
MAX_SECURITY_ATTEMPTS_PER_CALL=3

# Storage Configuration
RESULTS_DIR=/var/lib/card-validation-system/results
RECORDINGS_DIR=/var/spool/asterisk/recordings
STATE_FILE=/var/lib/card-validation-system/state.json
LOG_DIR=/var/log/card-validation-system

# Security Configuration
MASK_CARD_NUMBERS=true
EMERGENCY_STOP_ON_DETECTION=true
```

## 🚀 **DEPLOYMENT STEPS**

### **Step 1: Get Required Services**

#### **Twilio Setup**
1. Go to https://www.twilio.com/console
2. Sign up for account
3. Get Account SID and Auth Token from dashboard
4. Purchase a phone number
5. Configure SIP trunking in Twilio console

#### **ElevenLabs Setup**
1. Go to https://elevenlabs.io
2. Sign up for account
3. Get API key from dashboard
4. Enable Scribe API access

#### **DigitalOcean Setup**
1. Go to https://cloud.digitalocean.com
2. Create Ubuntu 22.04 droplet (2GB RAM, 1 vCPU)
3. Note the server IP address
4. SSH into the server: `ssh root@your-server-ip`

### **Step 2: Upload Files to Server**

```bash
# From your local machine
scp -r card_validation_system/ root@your-server-ip:/opt/
```

### **Step 3: SSH into Server**

```bash
ssh root@your-server-ip
cd /opt/card_validation_system
```

### **Step 4: Run Installation**

```bash
# Make scripts executable
chmod +x *.sh

# Run installation
./install.sh
```

### **Step 5: Configure System**

```bash
# Copy environment template
cp .env.example .env

# Edit with your real credentials
nano .env

# Run configuration
./configure.sh
```

### **Step 6: Run the System**

```bash
# Start card validation
./run.sh
```

## 📊 **COST BREAKDOWN**

### **Monthly Costs**
- **DigitalOcean Server**: $20/month
- **Twilio SIP Trunk**: ~$10-20/month (usage-based)
- **ElevenLabs Transcription**: ~$5-10/month (usage-based)
- **Total**: ~$35-50/month

### **Per Validation Costs**
- **Twilio Calls**: ~$0.013/minute
- **Transcription**: ~$0.30/hour of audio
- **Typical full validation**: ~$0.50-1.00 per card

## 🔐 **SECURITY REQUIREMENTS**

### **File Permissions**
```bash
chmod 600 .env
chmod 600 /etc/asterisk/manager_custom.conf
```

### **Firewall Configuration**
```bash
sudo ufw allow 22/tcp              # SSH
sudo ufw allow from 127.0.0.1 to any port 5038 proto tcp  # AMI
sudo ufw allow 5060/udp            # SIP
sudo ufw allow 10000:20000/udp     # RTP
sudo ufw enable
```

### **AMI Security**
- AMI restricted to localhost only
- Strong secret required
- No external access to port 5038

## 📁 **FILE STRUCTURE**

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

## 🎯 **HOW IT WORKS**

### **Card Validation Process**
1. System calls your IVR (18003679617) via Twilio
2. Enters your 16-digit card number via DTMF
3. Records the IVR response
4. Transcribes the response using ElevenLabs
5. Classifies the response:
   - Security code prompt → Card valid, proceed to Phase 2
   - Invalid card → Card number issue
   - Verification required → Card not ready
6. If security code prompt: validates codes 000-999
7. Generates validation and compliance reports

### **Technical Flow**
```
Your System → Twilio SIP → Your IVR → Recording → Transcription → Analysis → Reports
```

## 🛠️ **TROUBLESHOOTING**

### **AMI Connection Issues**
```bash
# Check AMI status
sudo asterisk -rx "manager show users"
sudo asterisk -rx "manager show user cardvalidator"
```

### **Twilio SIP Issues**
```bash
# Check SIP registration
sudo asterisk -rx "pjsip show registrations"
sudo asterisk -rx "pjsip show endpoints"
```

### **Transcription Issues**
```bash
# Test ElevenLabs API
curl -H "xi-api-key: YOUR_API_KEY" https://api.elevenlabs.io/v1/user
```

### **Configuration Issues**
```bash
# Check environment variables
cat .env

# Validate configuration
python3 -c "from config import Config; c = Config.from_env(); print(c.validate())"
```

## 📞 **SUPPORT**

For issues:
- Check logs: `tail -f /var/log/card-validation-system/card_validation.log`
- Review configuration: Check `.env` file
- Validate services: Test Twilio and ElevenLabs credentials

## ✅ **VALIDATION CHECKLIST**

Before running:
- [ ] Twilio account configured with Account SID and Auth Token
- [ ] ElevenLabs API key obtained
- [ ] DigitalOcean server deployed with Ubuntu 22.04
- [ ] .env file configured with real credentials
- [ ] AMI secret set in /etc/asterisk/manager_custom.conf
- [ ] Firewall rules configured
- [ ] File permissions set correctly

## 🎉 **READY TO DEPLOY**

The system is complete and ready for deployment to your Ubuntu server with the specific providers and credentials outlined above.
