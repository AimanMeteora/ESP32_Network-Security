#!/usr/bin/env python3
"""
convert_certs.py
────────────────
Reads certs/server.crt and certs/server.key and prints
them formatted as Arduino C raw-string literals ready to
paste into esp32_modbus_servo_tls.ino.

Usage:
    python3 convert_certs.py
"""

import os
import sys

CERT_PATH = os.path.join("certs", "server.crt")
KEY_PATH  = os.path.join("certs", "server.key")


def read_pem(path: str) -> str:
    if not os.path.exists(path):
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        print("  Run ./gen_certs.sh first.", file=sys.stderr)
        sys.exit(1)
    with open(path, "r") as fh:
        return fh.read().strip()


def to_c_raw_string(name: str, pem: str) -> str:
    return (
        f'const char {name}[] = R"(\n'
        f'{pem}\n'
        f')";\n'
    )


def main():
    cert_pem = read_pem(CERT_PATH)
    key_pem  = read_pem(KEY_PATH)

    cert_c = to_c_raw_string("SERVER_CERT", cert_pem)
    key_c  = to_c_raw_string("SERVER_KEY",  key_pem)

    output = (
        "// ── Paste the block below into esp32_modbus_servo_tls.ino ──────────────────\n\n"
        + cert_c
        + "\n"
        + key_c
        + "\n// ────────────────────────────────────────────────────────────────────────────\n"
    )

    print(output)

    # Also write to a file for convenience
    out_path = "certs_for_esp32.h"
    with open(out_path, "w") as fh:
        fh.write(output)
    print(f"[INFO] Also saved to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
