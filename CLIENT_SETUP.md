# Card Validation System — Client Deployment Guide

Version 1.0

This document is the complete deployment and operation manual for the
Card Validation System. It explains every step from a bare Ubuntu server
to a working installation, in order. Every command the client needs to
run is shown inside a code block. On GitHub, each code block has a small
copy button in its top-right corner — click it, and the whole block is
copied to the clipboard. Paste it into the terminal, press Enter, and the
step runs.

Nothing in this document executes automatically. Every command here is
run by the client, one step at a time.

---

## 1. Introduction

### What this system is

The Card Validation System places an automated telephone call to a bank's
Interactive Voice Response (IVR) menu, listens to the prompts, sends
phone key presses at the correct moment, and records the entire call.
When the call ends, it writes a report with the transcript, the audio
file path, and the call result.

### What the system does

1. Places an outbound call to a configured IVR number.
2. Listens to the IVR voice and transcribes it in real time.
3. Decides when to send a key press, and sends it.
4. Records every second of audio into a WAV file.
5. Writes a JSON report with the transcript, DTMF sent, and result.

### What the client is going to set up

- A Linux server with Asterisk installed.
- A Python environment running the project code.
- A SignalWire SIP trunk connection.
- Deepgram and ElevenLabs API keys for transcription.
- A configuration tying all of the above together.

---

## 2. Before installation

### Server requirements

- Ubuntu 24.04 or newer
- At least 2 GB RAM
- At least 20 GB free disk
- A public IPv4 address
- Root or sudo access

### Network requirements

Ports to open on the OS firewall and on the cloud provider:

| Port | Protocol | Purpose |
|---|---|---|
| 22 | TCP | SSH |
| 5060 | UDP | SIP signalling |
| 10000-20000 | UDP | RTP audio |

### Accounts and credentials needed

| Credential | Where to get it |
|---|---|
| SignalWire SIP username | SignalWire dashboard |
| SignalWire SIP password | SignalWire dashboard |
| SignalWire SIP domain | SignalWire dashboard |
| SignalWire outbound caller ID | SignalWire dashboard |
| Deepgram API key | console.deepgram.com |
| ElevenLabs API key | elevenlabs.io |
| Card number | Provided by client |
| IVR phone number | Provided by client |

---


## 3. Step 1 — Prepare the server

This step installs the packages the system needs.

### Command

    sudo apt-get update -y
    sudo apt-get install -y asterisk asterisk-core-sounds-en-wav python3.12 python3.12-venv python3-pip sox curl unzip git tcpdump

### Verify

    asterisk -V
    python3.12 --version

Expected: Asterisk 20.6.0 and Python 3.12.x.

---

## 4. Step 2 — Download the project

    cd /home/ubuntu
    git clone https://github.com/memehost358-cloud/ClientIVR.git
    cd ClientIVR

---

## 5. Step 3 — Create the required directories

| Folder | Purpose |
|---|---|
| /var/spool/asterisk/monitor/ | WAV recordings |
| /var/lib/card-validation-system/results/ | JSON reports |
| /var/log/card-validation-system/ | Application logs |
| /home/ubuntu/ClientIVR/recordings/ | Local recordings |

    sudo mkdir -p /var/spool/asterisk/monitor
    sudo mkdir -p /var/lib/card-validation-system/results
    sudo mkdir -p /var/log/card-validation-system
    sudo mkdir -p /home/ubuntu/ClientIVR/recordings
    sudo chown -R asterisk:asterisk /var/spool/asterisk/monitor
    sudo chmod 2775 /var/spool/asterisk/monitor
    sudo chown -R ubuntu:ubuntu /var/lib/card-validation-system
    sudo chown -R ubuntu:ubuntu /var/log/card-validation-system
    sudo chown -R ubuntu:ubuntu /home/ubuntu/ClientIVR/recordings

---


## 6. Step 4 — Set up Python

    cd /home/ubuntu/ClientIVR
    python3.12 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt

Packages installed: panoramisk, requests, python-dotenv, flask.

---

## 7. Step 5 — Configure the environment file

    cd /home/ubuntu/ClientIVR
    cp .env.example .env
    chmod 600 .env
    nano .env

Fill in these values:

| Variable | What to put |
|---|---|
| DEEPGRAM_API_KEY | Your Deepgram key |
| ELEVENLABS_API_KEY | Your ElevenLabs key |
| AMI_SECRET | A random password. Must match manager.conf. |
| CARD_NUMBER | The card number to test |
| IVR_PHONE_NUMBER | The IVR phone number to call |
| PROV_CALLERID_SIGNALWIRE | Outbound caller ID number |
| SIGNALWIRE_SPACE | Your SignalWire SIP domain |

Save with Ctrl+O then Ctrl+X.

Do not commit .env to GitHub. It contains secrets.

---

## 8. Step 6 — Configure Asterisk

Copy the configuration files:

    cd /home/ubuntu/ClientIVR
    sudo cp deploy/asterisk/sip.conf        /etc/asterisk/sip.conf
    sudo cp deploy/asterisk/manager.conf    /etc/asterisk/manager.conf
    sudo cp deploy/asterisk/rtp.conf        /etc/asterisk/rtp.conf
    sudo cp deploy/asterisk/extensions.conf /etc/asterisk/extensions.conf
    sudo cp deploy/asterisk/logger.conf     /etc/asterisk/logger.conf
    sudo cp deploy/asterisk/modules.conf    /etc/asterisk/modules.conf
    sudo chown root:asterisk /etc/asterisk/sip.conf /etc/asterisk/manager.conf /etc/asterisk/rtp.conf
    sudo chmod 640 /etc/asterisk/sip.conf /etc/asterisk/manager.conf /etc/asterisk/rtp.conf
    sudo chmod 644 /etc/asterisk/extensions.conf /etc/asterisk/logger.conf /etc/asterisk/modules.conf

### Edit sip.conf

    sudo nano /etc/asterisk/sip.conf

Change:

- The register line: replace SIP username, password, and domain.
- The [signalwire] block: replace username, secret, host, fromdomain.
- externaddr — set to the server's public IP.
- localnet — set to the private network (Oracle Cloud usually 10.0.0.0/255.255.255.0).

Save and exit.

### Edit manager.conf

    sudo nano /etc/asterisk/manager.conf

Change the secret under [cardvalidator] to a strong password. Use the
same value for AMI_SECRET in .env.

Save and exit.

---


## 9. Step 7 — Configure SignalWire

Confirm in the SignalWire dashboard:

1. The SIP subproject exists with a username and password.
2. The outbound caller ID number is assigned.
3. The IVR number is reachable through the trunk.
4. If SignalWire requires the server IP to be whitelisted, add it.

Write down the SIP username, password, and domain. These go into sip.conf.

---

## 10. Step 8 — Start and verify Asterisk

    sudo systemctl restart asterisk
    sleep 3
    sudo systemctl status asterisk

Expected: Active: active (running).

Check SIP registration:

    sudo asterisk -rx "sip show registry"

Expected: the word Registered on the SignalWire line.

If it says Request Sent or Unregistered, the SIP credentials are wrong
or outbound UDP 5060 is blocked.

---

## 11. Step 9 — Firewall

    sudo ufw allow 22/tcp
    sudo ufw allow 5060/udp
    sudo ufw allow 10000:20000/udp
    sudo ufw enable

Open the same ports on the cloud provider firewall.

---

## 12. Step 10 — Run the system

    cd /home/ubuntu/ClientIVR
    source venv/bin/activate
    python3 main.py 2>&1 | tee /tmp/call.log

The call runs for up to 180 seconds and ends by itself.

---

## 13. Step 11 — How a call flows through the system

    Start the application
            |
            v
    Asterisk running and registered to SignalWire
            |
            v
    Application places outbound call to the IVR
            |
            v
    The bank IVR answers the call
            |
            v
    Audio flows between server and IVR (RTP)
            |
            v
    Deepgram transcribes the audio live
            |
            v
    Application reads the transcript and matches trigger phrases
            |
            v
    When a trigger matches, the application sends DTMF over AMI
            |
            v
    Asterisk transmits the digit to the IVR
            |
            v
    The IVR responds and the next prompt begins
            |
            v
    When the IVR asks for the card, the application sends the card
            |
            v
    Call ends
            |
            v
    Asterisk closes the WAV recording
            |
            v
    Application writes a JSON report with the transcript, DTMF sent,
    and call result

---


## 14. How to monitor the system

While the application runs, every event prints to the terminal.

- Transcript: ... — what the IVR is saying
- ReactiveEngine: SEND DTMF ... — every key press we send
- ReactiveEngine: tick file_size=... — recording size growing
- ReactiveIVR DialBegin — the call being placed
- ReactiveIVR DialEnd ... DialStatus=ANSWER — the call answered
- ReactiveEngine result: ... — the call summary

---

## 15. How to find transcripts

    grep "Transcript:" /tmp/call.log

Filter to a section:

    grep -i "card number" /tmp/call.log
    grep -i "security code" /tmp/call.log

---

## 16. How to find DTMF activity

    grep "SEND DTMF" /tmp/call.log
    grep "DTMF redirect" /tmp/call.log
    grep "ReactiveEngine result" /tmp/call.log

---

## 17. How to find recordings

    ls -lht /var/spool/asterisk/monitor/*.wav

A working call produces hundreds of kilobytes. A failed call produces 44 bytes.

---

## 18. How to download recordings

From your own computer:

    scp ubuntu@SERVER_PUBLIC_IP:/var/spool/asterisk/monitor/*.wav .

Replace SERVER_PUBLIC_IP with the server's public IP.

---

## 19. File and directory locations

| Path | Purpose |
|---|---|
| /home/ubuntu/ClientIVR/ | Project folder |
| /home/ubuntu/ClientIVR/.env | API keys and settings |
| /home/ubuntu/ClientIVR/venv/ | Python virtual environment |
| /home/ubuntu/ClientIVR/recordings/ | Local recordings |
| /etc/asterisk/sip.conf | SignalWire SIP trunk |
| /etc/asterisk/manager.conf | AMI credentials |
| /etc/asterisk/rtp.conf | RTP port range |
| /etc/asterisk/extensions.conf | Dialplan |
| /etc/asterisk/logger.conf | Logging |
| /etc/asterisk/modules.conf | Module loading |
| /var/spool/asterisk/monitor/ | Recorded WAV files |
| /var/lib/card-validation-system/results/ | JSON reports |
| /var/log/card-validation-system/ | Application log |
| /tmp/call.log | Live call log |

---

## 20. Stop and restart

Stop the application:

    pkill -f main.py

Stop Asterisk:

    sudo systemctl stop asterisk

Restart Asterisk:

    sudo systemctl start asterisk

Restart the application:

    cd /home/ubuntu/ClientIVR
    source venv/bin/activate
    python3 main.py

---


## 21. Troubleshooting

### Asterisk not running

    sudo systemctl status asterisk
    sudo systemctl start asterisk

### SIP not registered

    sudo asterisk -rx "sip show registry"

If it says Request Sent or Unregistered:

1. Check SIP username and password in /etc/asterisk/sip.conf.
2. Check outbound UDP 5060 is not blocked.
3. Reload: sudo asterisk -rx "sip reload"

### No audio

1. Confirm UDP 10000-20000 is open on the OS and cloud firewall.
2. Confirm externaddr is set to the public IP.

### Cannot connect to AMI

    sudo ss -tlnp | grep 5038

Confirm the AMI secret in .env matches manager.conf.

### DTMF not reaching the IVR

This is a known issue on Ubuntu 24.04 with Asterisk 20.6. Confirm with a
packet capture:

    sudo tcpdump -ni ens3 -s 0 'udp' -w /tmp/capture.pcap &
    cd /home/ubuntu/ClientIVR
    source venv/bin/activate
    python3 main.py
    sudo pkill tcpdump
    sudo tcpdump -nr /tmp/capture.pcap -v | grep -c "telephone-event"

If the count is 0, Asterisk is not transmitting DTMF. Send the capture
to SignalWire support.

### No recording

Check /var/spool/asterisk/monitor/ exists and is writable by the
asterisk user.

### Python dependency problem

    cd /home/ubuntu/ClientIVR
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt

### Firewall problem

    sudo ufw status

You should see allow rules for TCP 22, UDP 5060, UDP 10000-20000.

---

## 22. Known limitations

- DTMF transmission. On this Ubuntu 24.04 with Asterisk 20.6, the key
  press is accepted by Asterisk and returned as Success, but no DTMF
  packet is transmitted to the IVR. Under investigation with SignalWire
  support.
- IVR behaviour varies. Some calls reach the card-number prompt. Some
  are transferred to a live agent. The IVR decides this.
- One call per run. Each python3 main.py processes a single call.

---

End of document
