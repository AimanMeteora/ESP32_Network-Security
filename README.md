# ESP32 Servo Control via Modbus TCP + TLS
## Complete Setup Guide

---

## File Overview

| File | Purpose |
|------|---------|
| `esp32_modbus_servo_plain.ino` | Phase 1 — Plain TCP Modbus server + servo |
| `esp32_modbus_servo_tls.ino`   | Phase 2 — TLS-encrypted Modbus server + servo |
| `gen_certs.sh`                 | Generate self-signed TLS certificate & key |
| `convert_certs.py`             | Convert PEM certs → C strings for the .ino |
| `modbus_tls_client.py`         | Phase 3 — Python TLS client / OpenPLC bridge |
| `verify_encryption.py`         | Step 10 — Wireshark encryption verification |

---

## Phase 1 — Plain TCP Modbus + Servo

### Hardware wiring

```
Servo Signal  →  GPIO18  (ESP32)
Servo VCC     →  External 5V supply
Servo GND     →  GND (shared with ESP32)
```

### Arduino dependencies
Install via Arduino Library Manager:
- **ESP32Servo**  (by Kevin Harrington / John K. Bennett)

### Setup
1. Open `esp32_modbus_servo_plain.ino` in Arduino IDE.
2. Set your Wi-Fi credentials:
   ```cpp
   const char* WIFI_SSID     = "YOUR_SSID";
   const char* WIFI_PASSWORD = "YOUR_PASSWORD";
   ```
3. Select board: `ESP32 Dev Module`
4. Upload.

### Serial Monitor output (expected)
```
Connecting to MyWiFi........
WiFi connected. IP: 192.168.1.100
[SERVO] initialised at 0°
[MODBUS] Listening on port 502
```

### Test with OpenPLC
Map output variables:
```
%QW0  →  Holding Register 0  (servo angle, 0-180)
%QW1  →  Holding Register 1  (step delay ms, 0 = instant)
```
Write `%QW0 = 90` — servo should move to 90°.

---

## Phase 2 — TLS-encrypted Modbus

### Step 1: Generate certificates (Ubuntu)
```bash
chmod +x gen_certs.sh
./gen_certs.sh
# Creates certs/server.key and certs/server.crt
```

### Step 2: Convert to C strings
```bash
python3 convert_certs.py
# Prints C-string format and saves certs_for_esp32.h
```

### Step 3: Embed in firmware
Copy the `SERVER_CERT` and `SERVER_KEY` values from the output
into `esp32_modbus_servo_tls.ino`, replacing the placeholder text.

### Step 4: Update the IP in the certificate
The `gen_certs.sh` script uses CN=192.168.1.100 by default.
Change it to match your ESP32's actual IP:
```bash
# Edit gen_certs.sh line:
-subj ".../CN=<YOUR_ESP32_IP>"
```

### Step 5: Upload TLS firmware
Open `esp32_modbus_servo_tls.ino`, set your Wi-Fi credentials,
and upload to the ESP32.

Serial Monitor output (expected):
```
WiFi connected. IP: 192.168.1.100
[SERVO] initialised at 0°
[TLS-MODBUS] Listening on port 8502
```

---

## Phase 3 — Python TLS Client

### Install dependencies
```bash
pip install pymodbus
```

### Interactive mode (manual testing)
```bash
python3 modbus_tls_client.py \
    --mode interactive \
    --esp32-host 192.168.1.100 \
    --esp32-port 8502 \
    --cert certs/server.crt
```

Then type angles at the prompt:
```
angle [speed] > 90
  ✓ HR0=90° HR1=0ms/step  →  ESP32

angle [speed] > 45 10
  ✓ HR0=45° HR1=10ms/step  →  ESP32

angle [speed] > read
  HR0 (angle) = 45°
  HR1 (speed) = 10 ms/step
```

### Bridge mode (for OpenPLC)
```bash
python3 modbus_tls_client.py \
    --mode bridge \
    --esp32-host 192.168.1.100 \
    --esp32-port 8502 \
    --cert certs/server.crt \
    --listen-port 5020
```

Point OpenPLC's Modbus master at `127.0.0.1:5020`.
The bridge transparently re-encrypts every write to the ESP32.

```
OpenPLC → plain TCP :5020 → Python bridge → TLS :8502 → ESP32 → Servo
```

---

## Step 10 — Verify encryption with Wireshark

### Install Wireshark
```bash
sudo apt install wireshark
```

### Capture filter
Start a capture on your Wi-Fi adapter with:
```
tcp.port == 502 or tcp.port == 8502
```

### Run the verification script
```bash
python3 verify_encryption.py
```

Follow the prompts. Expected results:

| Connection | Wireshark shows |
|------------|----------------|
| Plain TCP (port 502) | `Modbus TCP` frames with readable register address and value |
| TLS (port 8502) | `TLS Handshake` + `Application Data` — no readable values |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Servo jitters | Increase `SERVO_MIN_US`/`SERVO_MAX_US` to match your servo datasheet |
| TLS handshake fails | Confirm `server.crt` CN matches the ESP32 IP; regenerate if needed |
| `ssl.SSLCertVerificationError` | Pass the correct `--cert` path to the Python client |
| OpenPLC can't connect | In bridge mode, verify `--listen-port` matches OpenPLC's configured IP/port |
| ESP32 not found on network | Check Wi-Fi credentials; Serial Monitor will show the assigned IP |

---

## System Architecture

```
               OpenPLC Ladder Logic
                        │
                Writes %QW0 / %QW1
                        │
                        ▼
          Python modbus_tls_client.py
          (bridge mode on port 5020)
                        │
              TLS-encrypted channel
                        │
        ────────────────────────────────
                   Wi-Fi Network
        ────────────────────────────────
                        │
                        ▼
          ESP32 (port 8502, TLS server)
          esp32_modbus_servo_tls.ino
                        │
            Reads HR0 (angle), HR1 (speed)
                        │
                        ▼
               servo.write(angle)
                        │
                        ▼
                  Servo Motor Moves
```
