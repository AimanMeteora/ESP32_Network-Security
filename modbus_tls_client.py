#!/usr/bin/env python3
"""
modbus_tls_client.py
─────────────────────
Phase 3 — Python TLS-Modbus bridge

Architecture:
    OpenPLC → (calls this script / shared state) → TLS → ESP32 → Servo

This script exposes two modes:

  1. INTERACTIVE  — type angles manually in a terminal (good for testing)
  2. OPENPLC MODE — OpenPLC writes a value to a local plain-TCP Modbus
                    server that this script creates; the script then
                    forwards it to the ESP32 over TLS.

Usage:
    pip install pymodbus
    python3 modbus_tls_client.py --mode interactive
    python3 modbus_tls_client.py --mode bridge --listen-port 5020

Configuration (edit the constants below or pass CLI args):
    ESP32_HOST    — IP address of the ESP32 on your LAN
    ESP32_PORT    — TLS port on the ESP32 (default 8502)
    CERT_PATH     — Path to server.crt (used for server verification)
"""

import argparse
import logging
import ssl
import socket
import struct
import sys
import time
import threading

# ── Configuration ─────────────────────────────────────────────────────────────
ESP32_HOST  = "192.168.1.100"   # ← change to your ESP32's IP
ESP32_PORT  = 8502              # ← must match MODBUS_PORT in the .ino
CERT_PATH   = "certs/server.crt"  # self-signed cert for verification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("modbus-tls")


# ── Low-level Modbus TCP helpers ──────────────────────────────────────────────

class ModbusTLSClient:
    """Minimal Modbus TCP client that runs over a TLS socket."""

    def __init__(self, host: str, port: int, cert_path: str):
        self.host      = host
        self.port      = port
        self.cert_path = cert_path
        self._sock     = None
        self._trans_id = 0

    # ── Connection management ─────────────────────────────────────────────────
    def connect(self) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False          # self-signed cert, no hostname
        ctx.verify_mode    = ssl.CERT_REQUIRED
        ctx.load_verify_locations(self.cert_path)

        raw = socket.create_connection((self.host, self.port), timeout=5)
        self._sock = ctx.wrap_socket(raw, server_hostname=self.host)
        log.info("TLS connected to %s:%d", self.host, self.port)

        # Log cipher for verification
        cipher = self._sock.cipher()
        log.info("Cipher: %s / Protocol: %s", cipher[0], cipher[1])

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            log.info("Disconnected")

    def is_connected(self) -> bool:
        return self._sock is not None

    # ── Modbus frame builder ──────────────────────────────────────────────────
    def _next_tid(self) -> int:
        self._trans_id = (self._trans_id + 1) & 0xFFFF
        return self._trans_id

    def _build_mbap(self, pdu: bytes, unit_id: int = 1) -> bytes:
        tid    = self._next_tid()
        length = len(pdu) + 1          # pdu + unit id
        return struct.pack(">HHHB", tid, 0, length, unit_id) + pdu

    def _send_recv(self, pdu: bytes, unit_id: int = 1) -> bytes:
        frame = self._build_mbap(pdu, unit_id)
        self._sock.sendall(frame)

        # Read MBAP header (7 bytes)
        header = self._recv_exact(7)
        length = struct.unpack(">H", header[4:6])[0]
        body   = self._recv_exact(length - 1)   # minus unit-id byte in header
        return body

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Socket closed by remote")
            buf += chunk
        return buf

    # ── Public Modbus operations ──────────────────────────────────────────────
    def write_single_register(self, address: int, value: int,
                               unit_id: int = 1) -> bool:
        """FC06 — Write a single holding register."""
        pdu  = struct.pack(">BHH", 0x06, address, value)
        resp = self._send_recv(pdu, unit_id)
        if resp[0] & 0x80:
            log.error("FC06 exception code: %02X", resp[1])
            return False
        log.debug("FC06 HR%d = %d  OK", address, value)
        return True

    def write_multiple_registers(self, start: int, values: list,
                                  unit_id: int = 1) -> bool:
        """FC16 — Write multiple holding registers."""
        count    = len(values)
        byte_cnt = count * 2
        data     = struct.pack(f">{count}H", *values)
        pdu      = struct.pack(">BHHB", 0x10, start, count, byte_cnt) + data
        resp     = self._send_recv(pdu, unit_id)
        if resp[0] & 0x80:
            log.error("FC16 exception code: %02X", resp[1])
            return False
        log.debug("FC16 HR%d..%d = %s  OK", start, start + count - 1, values)
        return True

    def read_holding_registers(self, start: int, count: int,
                                unit_id: int = 1) -> list:
        """FC03 — Read holding registers, returns list of ints."""
        pdu  = struct.pack(">BHH", 0x03, start, count)
        resp = self._send_recv(pdu, unit_id)
        if resp[0] & 0x80:
            log.error("FC03 exception code: %02X", resp[1])
            return []
        byte_cnt = resp[1]
        regs     = list(struct.unpack(f">{byte_cnt//2}H", resp[2:2+byte_cnt]))
        return regs


# ── Mode 1: Interactive console ───────────────────────────────────────────────
def run_interactive(client: ModbusTLSClient) -> None:
    """Let the user type angles manually to test the servo."""
    print("\n── Interactive Modbus TLS Client ─────────────────────")
    print("Commands:")
    print("  <angle>         — set HR0 (servo angle, 0-180)")
    print("  <angle> <speed> — set HR0 + HR1 (angle + step delay ms)")
    print("  read            — read HR0 and HR1")
    print("  quit            — exit")
    print("─────────────────────────────────────────────────────\n")

    while True:
        try:
            line = input("angle [speed] > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            continue

        if line == "quit":
            break

        if line == "read":
            regs = client.read_holding_registers(0, 2)
            if regs:
                print(f"  HR0 (angle) = {regs[0]}°")
                print(f"  HR1 (speed) = {regs[1]} ms/step")
            continue

        parts = line.split()
        try:
            angle = int(parts[0])
            speed = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            print("  Invalid input. Enter an integer angle (0-180).")
            continue

        if not 0 <= angle <= 180:
            print("  Angle must be 0–180.")
            continue

        ok = client.write_multiple_registers(0, [angle, speed])
        if ok:
            print(f"  ✓ HR0={angle}° HR1={speed}ms/step  →  ESP32")
        else:
            print("  ✗ Write failed.")


# ── Mode 2: Bridge — expose a plain Modbus TCP server for OpenPLC ─────────────
#
# OpenPLC connects to this bridge on a plain (unencrypted) port.
# The bridge forwards every write to the ESP32 over TLS.
#
# OpenPLC mapping:
#   %QW0 → HR0 (angle)
#   %QW1 → HR1 (speed)

class ModbusBridgeServer:
    """
    Plain-TCP Modbus server (for OpenPLC) that transparently
    forwards holding register writes to the ESP32 over TLS.
    """

    def __init__(self, listen_port: int, tls_client: ModbusTLSClient):
        self.listen_port = listen_port
        self.tls_client  = tls_client
        self.registers   = [0, 0]        # local mirror of HR0..HR1
        self._running    = False

    def start(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.listen_port))
        srv.listen(5)
        self._running = True
        log.info("Bridge listening on plain TCP port %d", self.listen_port)
        log.info("Point OpenPLC Modbus master at  127.0.0.1:%d", self.listen_port)

        while self._running:
            try:
                conn, addr = srv.accept()
                log.info("OpenPLC connected from %s", addr)
                t = threading.Thread(
                    target=self._handle_client, args=(conn,), daemon=True
                )
                t.start()
            except Exception as exc:
                log.error("Accept error: %s", exc)

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(2)
        try:
            while True:
                header = self._recv_exact(conn, 7)
                if not header:
                    break
                length = struct.unpack(">H", header[4:6])[0]
                body   = self._recv_exact(conn, length - 1)
                if body is None:
                    break

                response = self._process(header, body)
                conn.sendall(response)
        except Exception as exc:
            log.debug("Client handler: %s", exc)
        finally:
            conn.close()
            log.info("OpenPLC disconnected")

    def _recv_exact(self, sock: socket.socket, n: int):
        buf = b""
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
            except socket.timeout:
                return None
            if not chunk:
                return None
            buf += chunk
        return buf

    def _process(self, header: bytes, body: bytes) -> bytes:
        func  = body[0]
        unit  = header[6]

        if func == 0x03:   # Read Holding Registers
            start = struct.unpack(">H", body[1:3])[0]
            count = struct.unpack(">H", body[3:5])[0]
            data  = struct.pack(f">{count}H",
                                *self.registers[start:start+count])
            pdu   = bytes([func, count * 2]) + data

        elif func == 0x06:  # Write Single Register
            addr  = struct.unpack(">H", body[1:3])[0]
            val   = struct.unpack(">H", body[3:5])[0]
            if addr < len(self.registers):
                self.registers[addr] = val
                self._forward_to_esp32()
            pdu = body   # echo back

        elif func == 0x10:  # Write Multiple Registers
            start = struct.unpack(">H", body[1:3])[0]
            count = struct.unpack(">H", body[3:5])[0]
            vals  = list(struct.unpack(f">{count}H", body[6:6+count*2]))
            for i, v in enumerate(vals):
                if start + i < len(self.registers):
                    self.registers[start + i] = v
            self._forward_to_esp32()
            pdu = bytes([func]) + body[1:5]   # echo start + count

        else:
            pdu = bytes([func | 0x80, 0x01])  # illegal function

        length = len(pdu) + 1
        return header[:4] + struct.pack(">H", length) + bytes([unit]) + pdu

    def _forward_to_esp32(self) -> None:
        angle = self.registers[0]
        speed = self.registers[1]
        log.info("Forwarding to ESP32 via TLS: HR0=%d HR1=%d", angle, speed)
        try:
            ok = self.tls_client.write_multiple_registers(0, [angle, speed])
            if not ok:
                log.warning("TLS write returned failure")
        except Exception as exc:
            log.error("TLS forward error: %s — attempting reconnect", exc)
            try:
                self.tls_client.disconnect()
                self.tls_client.connect()
                self.tls_client.write_multiple_registers(0, [angle, speed])
            except Exception as exc2:
                log.error("Reconnect failed: %s", exc2)


# ── Entry point ───────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Modbus TLS bridge/client for ESP32 servo")
    p.add_argument("--mode", choices=["interactive", "bridge"],
                   default="interactive",
                   help="interactive: manual terminal control; "
                        "bridge: forward OpenPLC to ESP32 over TLS")
    p.add_argument("--esp32-host", default=ESP32_HOST,
                   help=f"ESP32 IP address (default: {ESP32_HOST})")
    p.add_argument("--esp32-port", type=int, default=ESP32_PORT,
                   help=f"ESP32 TLS port (default: {ESP32_PORT})")
    p.add_argument("--cert", default=CERT_PATH,
                   help=f"Path to server.crt (default: {CERT_PATH})")
    p.add_argument("--listen-port", type=int, default=5020,
                   help="Local plain-TCP port for OpenPLC (bridge mode, default 5020)")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    client = ModbusTLSClient(
        host      = args.esp32_host,
        port      = args.esp32_port,
        cert_path = args.cert,
    )

    log.info("Connecting to ESP32 at %s:%d …", args.esp32_host, args.esp32_port)
    try:
        client.connect()
    except Exception as exc:
        log.error("Connection failed: %s", exc)
        sys.exit(1)

    try:
        if args.mode == "interactive":
            run_interactive(client)
        else:
            bridge = ModbusBridgeServer(args.listen_port, client)
            bridge.start()          # blocks forever
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
