#!/usr/bin/env python3
"""
verify_encryption.py
─────────────────────
Step 10 — Verify that TLS is actually encrypting the traffic.

This script does TWO things:

1. Sends a known plaintext Modbus write (angle = 137) WITHOUT TLS
   so you can confirm Wireshark sees the value in the clear.

2. Sends the same write WITH TLS and confirms the value is NOT
   visible as plaintext.

Run it while Wireshark is capturing on your Wi-Fi adapter
with filter:   tcp.port == 502 or tcp.port == 8502

Requirements:
    pip install pymodbus scapy
"""

import socket
import ssl
import struct
import sys
import time

# ── Settings — edit to match your setup ───────────────────────────────────────
ESP32_HOST       = "192.168.1.100"
PLAIN_PORT       = 502     # Phase 1 plain-TCP port
TLS_PORT         = 8502    # Phase 2 TLS port
CERT_PATH        = "certs/server.crt"

TEST_ANGLE       = 137     # distinctive value to spot in Wireshark
TEST_UNIT_ID     = 1
TEST_HR_ADDRESS  = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def build_fc06(address: int, value: int, tid: int = 1, unit: int = 1) -> bytes:
    pdu    = struct.pack(">BHH", 0x06, address, value)
    length = len(pdu) + 1
    return struct.pack(">HHHB", tid, 0, length, unit) + pdu


def send_plain(host: str, port: int, data: bytes) -> bytes:
    with socket.create_connection((host, port), timeout=5) as s:
        s.sendall(data)
        return s.recv(256)


def send_tls(host: str, port: int, cert: str, data: bytes) -> bytes:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cert)
    with socket.create_connection((host, port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as s:
            s.sendall(data)
            return s.recv(256)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    frame = build_fc06(TEST_HR_ADDRESS, TEST_ANGLE)

    print("=" * 60)
    print(f"Test value: HR{TEST_HR_ADDRESS} = {TEST_ANGLE}  (0x{TEST_ANGLE:04X})")
    print("=" * 60)
    print()
    print("In Wireshark, look for that value in the packet payload.")
    print(f"  Hex of angle {TEST_ANGLE}: {TEST_ANGLE.to_bytes(2, 'big').hex()}")
    print()

    # ── Test 1: Plain TCP ─────────────────────────────────────────────────────
    print(f"[1/2] Sending WITHOUT TLS  →  {ESP32_HOST}:{PLAIN_PORT}")
    print(f"      Wireshark filter: tcp.port == {PLAIN_PORT}")
    print("      You SHOULD see the Modbus payload in plain text.")
    input("      Press Enter when Wireshark is ready …")

    try:
        resp = send_plain(ESP32_HOST, PLAIN_PORT, frame)
        print(f"      Response (hex): {resp.hex()}")
        print("      ✓ Plain packet sent. Check Wireshark now.")
    except Exception as exc:
        print(f"      ✗ Error: {exc}")

    print()
    time.sleep(1)

    # ── Test 2: TLS ──────────────────────────────────────────────────────────
    print(f"[2/2] Sending WITH TLS     →  {ESP32_HOST}:{TLS_PORT}")
    print(f"      Wireshark filter: tcp.port == {TLS_PORT}")
    print("      You should see only 'TLS Application Data' — NO readable values.")
    input("      Press Enter when Wireshark is ready …")

    try:
        resp = send_tls(ESP32_HOST, TLS_PORT, CERT_PATH, frame)
        print(f"      Response (hex): {resp.hex()}")
        print("      ✓ TLS packet sent. Check Wireshark now.")
    except Exception as exc:
        print(f"      ✗ Error: {exc}")
        print("       Make sure esp32_modbus_servo_tls.ino is flashed and running.")

    print()
    print("=" * 60)
    print("Expected Wireshark results:")
    print(f"  Port {PLAIN_PORT}:  Modbus TCP frames visible, value {TEST_ANGLE} readable")
    print(f"  Port {TLS_PORT}: Only TLS Handshake + Encrypted Application Data")
    print("=" * 60)


if __name__ == "__main__":
    main()
