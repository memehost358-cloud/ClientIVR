#!/bin/bash
# Card Validation System Configuration Script
# Run this after installation to configure the system (deploy dialplan, check
# AMI, validate env vars). Works with SignalWire OR Twilio (or both) - no
# hard requirement for Twilio.

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "==================================="
echo "Card Validation System Configuration"
echo "==================================="
echo "Project root: $PROJECT_ROOT"

# Create .env from template if missing
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "Please edit .env file with your real credentials, then re-run:"
    echo "  nano .env"
    echo "  ./configure.sh"
    exit 1
fi

# Load env vars (some bash-quoted values might be complex; use safe load)
set -a
# shellcheck disable=SC1091
source .env
set +a

# -------- Validate required environment variables --------

echo "Validating configuration..."

# Split PROVIDER_ORDER into array
IFS=',' read -r -a PROVIDER_ARR <<< "${PROVIDER_ORDER:-signalwire}"
PROVIDER_ARR=("${PROVIDER_ARR[@]// /}") # trim whitespace
echo "Configured PROVIDER_ORDER: ${PROVIDER_ARR[*]}"

any_valid_provider=0
for p in "${PROVIDER_ARR[@]}"; do
    up="$(echo "$p" | tr '[:lower:]' '[:upper:]')"
    endpoint_var="PROV_ENDPOINT_${up}"
    callerid_var="PROV_CALLERID_${up}"
    missing=0
    [ -z "${!endpoint_var}" ] && echo "MISSING: $endpoint_var" && missing=1
    [ -z "${!callerid_var}" ] && echo "MISSING: $callerid_var" && missing=1

    # Look for credentials (either SW or Twilio or any future provider)
    case "$up" in
        SIGNALWIRE)
            [ -z "${SIGNALWIRE_ACCOUNT_SID}" ] && echo "WARN: SIGNALWIRE_ACCOUNT_SID not set (but Asterisk trunk OK)"
            [ -z "${SIGNALWIRE_AUTH_TOKEN}" ] && echo "WARN: SIGNALWIRE_AUTH_TOKEN not set (but Asterisk trunk OK)"
            ;;
        TWILIO)
            [ -z "${TWILIO_ACCOUNT_SID}" ] && echo "WARN: TWILIO_ACCOUNT_SID not set (but Asterisk trunk OK)"
            [ -z "${TWILIO_AUTH_TOKEN}" ] && echo "WARN: TWILIO_AUTH_TOKEN not set (but Asterisk trunk OK)"
            ;;
    esac

    if [ "$missing" = "0" ]; then
        any_valid_provider=1
    fi
done

required_vars=(
    "IVR_PHONE_NUMBER"
    "ELEVENLABS_API_KEY"
    "AMI_SECRET"
)
missing_vars=()
for var in "${required_vars[@]}"; do
    val="${!var}"
    if [ -z "$val" ] || [[ "$val" == *"your_"* ]] || [[ "$val" == *"YOUR_"* ]]; then
        missing_vars+=("$var")
    fi
done

# Validate card inventory
have_cards=0
if [ -n "${CARDS_JSON}" ] || [ -n "${CARD_NUMBERS_CSV}" ] || [ -n "${CARD_NUMBER}" ]; then
    have_cards=1
fi

if [ ${#missing_vars[@]} -gt 0 ] || [ "$any_valid_provider" = "0" ] || [ "$have_cards" = "0" ]; then
    echo "ERROR: required configuration missing:"
    if [ ${#missing_vars[@]} -gt 0 ]; then
        echo "  - Required vars NOT set: ${missing_vars[*]}"
    fi
    if [ "$any_valid_provider" = "0" ]; then
        echo "  - NO PROVIDER has both PROV_ENDPOINT_* and PROV_CALLERID_* set in PROVIDER_ORDER list"
    fi
    if [ "$have_cards" = "0" ]; then
        echo "  - NO CARD INVENTORY: set CARDS_JSON, CARD_NUMBERS_CSV, or CARD_NUMBER"
    fi
    echo "Please edit .env file and set these values, then re-run ./configure.sh"
    exit 1
fi

echo "✓ All required environment variables are configured"

# -------- Create required directories + ownership --------

if [ -x "$PROJECT_ROOT/fix_directories.sh" ]; then
    echo "Creating directories + fixing ownership..."
    bash "$PROJECT_ROOT/fix_directories.sh"
else
    echo "WARN: fix_directories.sh not executable; skipping dir creation"
fi

# -------- Configure Asterisk SIP trunk for Twilio (if Twilio configured) --------

TWILIO_CONFIGURED=0
for p in "${PROVIDER_ARR[@]}"; do
    if [ "$(echo "$p" | tr '[:lower:]' '[:upper:]')" = "TWILIO" ]; then
        TWILIO_CONFIGURED=1
        break
    fi
done

if [ "$TWILIO_CONFIGURED" = "1" ] && [ -n "${TWILIO_ACCOUNT_SID}" ] && [ -n "${TWILIO_AUTH_TOKEN}" ]; then
    echo "Configuring Asterisk SIP trunk for Twilio..."
    if [ -f asterisk/pjsip.conf ]; then
        sed \
            -e "s/TWILIO_ACCOUNT_SID/$TWILIO_ACCOUNT_SID/g" \
            -e "s/TWILIO_AUTH_TOKEN/$TWILIO_AUTH_TOKEN/g" \
            asterisk/pjsip.conf > /tmp/pjsip_clientivr.conf
        if command -v curl >/dev/null 2>&1; then
            PUB_IP="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || true)"
            if [ -n "$PUB_IP" ]; then
                sed -i "s/YOUR_SERVER_PUBLIC_IP/$PUB_IP/g" /tmp/pjsip_clientivr.conf
            fi
        fi
        sudo cp -v /tmp/pjsip_clientivr.conf /etc/asterisk/pjsip_custom.conf
        sudo chown asterisk:asterisk /etc/asterisk/pjsip_custom.conf
        sudo chmod 640 /etc/asterisk/pjsip_custom.conf
        rm -f /tmp/pjsip_clientivr.conf
    else
        echo "  -> asterisk/pjsip.conf template not found; skipping Twilio pjsip_custom.conf write"
    fi
fi

# -------- Deploy our dynamic dialplan --------
echo "Deploying Asterisk dialplan (card-validation context)..."
if [ -f asterisk/extensions.conf ]; then
    sudo cp -v asterisk/extensions.conf /etc/asterisk/extensions_custom.d/extensions.conf
    sudo chown asterisk:asterisk /etc/asterisk/extensions_custom.d/extensions.conf
    sudo chmod 640 /etc/asterisk/extensions_custom.d/extensions.conf
else
    echo "ERROR: asterisk/extensions.conf not found. Aborting."
    exit 2
fi

# -------- Reload Asterisk --------
echo "Reloading Asterisk configuration..."
if command -v asterisk >/dev/null 2>&1; then
    # Reload both SIP stacks + dialplan. (If only chan_sip is used, pjsip reload
    # is harmless and prints a warning.)
    sudo asterisk -rx "dialplan reload" 2>&1 | tail -n 5 || true
    sudo asterisk -rx "sip reload" 2>&1 | tail -n 5 || true
    sudo asterisk -rx "module reload res_pjsip.so" 2>&1 | tail -n 5 || true
    # Show the contexts the user just deployed (sanity check they exist).
    echo "Dialplan contexts deployed:"
    sudo asterisk -rx "dialplan show card-validation" 2>&1 | head -n 25 || true
else
    echo "WARN: asterisk binary not on PATH; skipping config reload"
fi

# -------- Test AMI connection using panoramisk --------
echo "Testing AMI connection..."
AMI_PYTEST_SCRIPT=$(cat << 'PYEOF'
import os, sys, asyncio, json
sys.path.insert(0, '.')
from panoramisk import Manager

async def test():
    host = os.environ.get('AMI_HOST', '127.0.0.1')
    port = int(os.environ.get('AMI_PORT', '5038'))
    user = os.environ.get('AMI_USERNAME', 'cardvalidator')
    secret = os.environ.get('AMI_SECRET', '')
    if not secret:
        print("FAIL: AMI_SECRET empty")
        sys.exit(5)
    m = Manager(host=host, port=port, username=user, secret=secret, ping_delay=5)
    try:
        await asyncio.wait_for(m.connect(), timeout=8)
    except Exception as e:
        print(f"FAIL: AMI connect error -> {e}")
        print("Troubleshoot:")
        print(f"  sudo asterisk -rx \"manager show users\"")
        print(f"  sudo asterisk -rx \"manager show user {user}\"")
        print(f"  sudo ss -tlnp | grep {port}")
        print(f"  cat /etc/asterisk/manager_custom.conf | grep -A 20 \"\\[{user}\\]\"")
        sys.exit(6)
    try:
        await asyncio.wait_for(m.send_action({'Action': 'Ping'}, timeout=5), timeout=8)
    except Exception as e:
        print(f"WARN: AMI ping failed post-connect: {e}")
    try:
        await m.close()
    except Exception:
        pass
    print(f"OK: AMI connected user={user} {host}:{port}")

asyncio.run(test())
PYEOF
)

if command -v python3 >/dev/null 2>&1; then
    if [ -f "$PROJECT_ROOT/venv/bin/python3" ]; then
        AMI_PY="$PROJECT_ROOT/venv/bin/python3"
    elif [ -f "/opt/ivr-tester/venv/bin/python3" ]; then
        AMI_PY="/opt/ivr-tester/venv/bin/python3"
    else
        AMI_PY="python3"
    fi
    set +e
    AMI_TEST_OUT="$("$AMI_PY" -c "$AMI_PYTEST_SCRIPT" 2>&1)"
    AMI_TEST_RC=$?
    set -e
    echo "$AMI_TEST_OUT"
    if [ "$AMI_TEST_RC" -ne 0 ]; then
        echo "AMI test failed (rc=$AMI_TEST_RC); continuing anyway but expect runtime failures until fixed."
    fi
else
    echo "SKIP: python3 not on PATH"
fi

echo "==================================="
echo "Configuration completed successfully!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. (optional) Run quick syntax sanity checks:"
echo "     cd $PROJECT_ROOT && python3 -m py_compile main.py config.py card_validator.py ivr_client.py transcription.py response_analyzer.py report_generator.py compliance_checker.py web_server.py && echo SYNTAX_OK"
echo "2. Start the system (single cheap Phase-1 + CVV window first!):"
echo "     ./run.sh 2>&1 | tee /tmp/ivr-run-\$(date +%Y%m%d-%H%M%S).log"
