#!/bin/bash
# gen_certs.sh — Generate self-signed TLS certificate + key for the ESP32
# Run on Ubuntu/Linux:
#   chmod +x gen_certs.sh
#   ./gen_certs.sh
#
# Output files:
#   server.key  — RSA private key (2048-bit)
#   server.crt  — Self-signed X.509 certificate (365 days)

set -e

OUTPUT_DIR="./certs"
mkdir -p "$OUTPUT_DIR"

echo "==> Generating 2048-bit RSA private key..."
openssl genrsa -out "$OUTPUT_DIR/server.key" 2048

echo ""
echo "==> Generating self-signed certificate (365 days)..."
echo "    Fill in the fields below. CN (Common Name) should be"
echo "    the ESP32's IP address or hostname."
echo ""

openssl req -new -x509 \
  -out  "$OUTPUT_DIR/server.crt" \
  -key  "$OUTPUT_DIR/server.key" \
  -days 365 \
  -subj "/C=SG/ST=Singapore/L=Singapore/O=ESP32Project/OU=IoT/CN=192.168.1.100"

echo ""
echo "==> Done. Files written to $OUTPUT_DIR/"
echo "    server.key  — private key  (keep secret, embed in ESP32)"
echo "    server.crt  — certificate  (share with Python client)"
echo ""
echo "==> Next step: run   python3 convert_certs.py"
echo "    to get the C-string versions for the .ino file."
