/*
 * ESP32 Modbus TCP Server + Servo Control (Phase 1 — Plain TCP)
 *
 * Wiring:
 *   Servo Signal -> GPIO18
 *   Servo VCC    -> External 5V
 *   Servo GND    -> GND (shared with ESP32)
 *
 * Holding Registers:
 *   HR0 = Target servo angle  (0–180)
 *   HR1 = Servo speed delay   (ms between degree steps, 0 = instant)
 *
 * OpenPLC mapping:
 *   %QW0 -> HR0 (angle)
 *   %QW1 -> HR1 (speed)
 */

#include <WiFi.h>
#include <ESP32Servo.h>

// ── Wi-Fi credentials ────────────────────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_SSID";
const char* WIFI_PASSWORD = "YOUR_PASSWORD";

// ── Modbus TCP settings ───────────────────────────────────────────────────────
const uint16_t MODBUS_PORT        = 502;   // standard Modbus TCP port
const uint8_t  MODBUS_UNIT_ID     = 1;
const uint16_t NUM_HOLDING_REGS   = 2;

// ── Servo settings ────────────────────────────────────────────────────────────
const int SERVO_PIN    = 18;
const int SERVO_MIN_US = 500;   // pulse width for 0°
const int SERVO_MAX_US = 2400;  // pulse width for 180°

// ── Globals ───────────────────────────────────────────────────────────────────
WiFiServer modbusServer(MODBUS_PORT);
Servo      myServo;

uint16_t holdingRegisters[NUM_HOLDING_REGS] = {0, 0};

int  currentAngle  = 0;
int  targetAngle   = 0;

// ── Modbus function codes ─────────────────────────────────────────────────────
const uint8_t FC_READ_HOLDING_REGS  = 0x03;
const uint8_t FC_WRITE_SINGLE_REG   = 0x06;
const uint8_t FC_WRITE_MULTIPLE_REGS = 0x10;

// ─────────────────────────────────────────────────────────────────────────────
void connectWiFi() {
  Serial.printf("\nConnecting to %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected. IP: %s\n", WiFi.localIP().toString().c_str());
}

// ── Build a Modbus exception response ────────────────────────────────────────
void sendException(WiFiClient& client, uint8_t* mbapHeader,
                   uint8_t funcCode, uint8_t exceptionCode) {
  uint8_t resp[9];
  memcpy(resp, mbapHeader, 6);          // copy transaction/protocol/length fields
  resp[4] = 0x00;
  resp[5] = 0x03;                       // length = 3
  resp[6] = mbapHeader[6];             // unit id
  resp[7] = funcCode | 0x80;           // error flag
  resp[8] = exceptionCode;
  client.write(resp, 9);
}

// ── Process one Modbus TCP request ───────────────────────────────────────────
void handleModbusRequest(WiFiClient& client) {
  // MBAP header = 7 bytes: TransID(2) + ProtoID(2) + Length(2) + UnitID(1)
  uint8_t header[7];
  if (client.readBytes(header, 7) != 7) return;

  uint16_t pduLength = (header[4] << 8) | header[5];
  if (pduLength < 2) return;

  uint8_t pdu[256];
  uint16_t dataLen = pduLength - 1;   // subtract unit ID byte already in header
  if (client.readBytes(pdu, dataLen) != dataLen) return;

  uint8_t funcCode = pdu[0];

  // ── FC 03: Read Holding Registers ────────────────────────────────────────
  if (funcCode == FC_READ_HOLDING_REGS) {
    uint16_t startReg = (pdu[1] << 8) | pdu[2];
    uint16_t regCount = (pdu[3] << 8) | pdu[4];

    if (startReg + regCount > NUM_HOLDING_REGS) {
      sendException(client, header, funcCode, 0x02);  // illegal data address
      return;
    }

    uint8_t resp[9 + regCount * 2];
    memcpy(resp, header, 6);
    resp[5] = 3 + regCount * 2;        // length field
    resp[6] = header[6];               // unit id
    resp[7] = funcCode;
    resp[8] = regCount * 2;            // byte count

    for (int i = 0; i < regCount; i++) {
      resp[9  + i*2] = holdingRegisters[startReg + i] >> 8;
      resp[10 + i*2] = holdingRegisters[startReg + i] & 0xFF;
    }
    client.write(resp, 9 + regCount * 2);
  }

  // ── FC 06: Write Single Register ─────────────────────────────────────────
  else if (funcCode == FC_WRITE_SINGLE_REG) {
    uint16_t regAddr = (pdu[1] << 8) | pdu[2];
    uint16_t regVal  = (pdu[3] << 8) | pdu[4];

    if (regAddr >= NUM_HOLDING_REGS) {
      sendException(client, header, funcCode, 0x02);
      return;
    }
    holdingRegisters[regAddr] = regVal;
    Serial.printf("[FC06] HR%u = %u\n", regAddr, regVal);

    // Echo back (standard FC06 response)
    uint8_t resp[12];
    memcpy(resp, header, 6);
    resp[5] = 6;
    resp[6] = header[6];
    memcpy(resp + 7, pdu, 5);
    client.write(resp, 12);
  }

  // ── FC 16: Write Multiple Registers ──────────────────────────────────────
  else if (funcCode == FC_WRITE_MULTIPLE_REGS) {
    uint16_t startReg  = (pdu[1] << 8) | pdu[2];
    uint16_t regCount  = (pdu[3] << 8) | pdu[4];
    // pdu[5] = byte count

    if (startReg + regCount > NUM_HOLDING_REGS) {
      sendException(client, header, funcCode, 0x02);
      return;
    }
    for (int i = 0; i < regCount; i++) {
      holdingRegisters[startReg + i] = (pdu[6 + i*2] << 8) | pdu[7 + i*2];
      Serial.printf("[FC16] HR%u = %u\n", startReg + i, holdingRegisters[startReg + i]);
    }

    uint8_t resp[12];
    memcpy(resp, header, 6);
    resp[5] = 6;
    resp[6] = header[6];
    resp[7] = funcCode;
    resp[8] = pdu[1]; resp[9]  = pdu[2];
    resp[10] = pdu[3]; resp[11] = pdu[4];
    client.write(resp, 12);
  }

  else {
    sendException(client, header, funcCode, 0x01);  // illegal function
  }
}

// ── Smooth servo movement using HR1 as step delay ────────────────────────────
void updateServo() {
  targetAngle = constrain((int)holdingRegisters[0], 0, 180);
  uint16_t speedDelay = holdingRegisters[1];   // ms per degree step

  if (targetAngle == currentAngle) return;

  if (speedDelay == 0) {
    // Instant move
    myServo.write(targetAngle);
    currentAngle = targetAngle;
    Serial.printf("[SERVO] instant -> %d°\n", currentAngle);
  } else {
    // Step one degree toward target
    int step = (targetAngle > currentAngle) ? 1 : -1;
    currentAngle += step;
    myServo.write(currentAngle);
    Serial.printf("[SERVO] step -> %d°\n", currentAngle);
    delay(speedDelay);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);

  // Servo init
  ESP32PWM::allocateTimer(0);
  myServo.setPeriodHertz(50);
  myServo.attach(SERVO_PIN, SERVO_MIN_US, SERVO_MAX_US);
  myServo.write(0);
  currentAngle = 0;
  Serial.println("[SERVO] initialised at 0°");

  // Wi-Fi
  connectWiFi();

  // Modbus TCP server
  modbusServer.begin();
  Serial.printf("[MODBUS] Listening on port %u\n", MODBUS_PORT);
}

void loop() {
  // Accept new client
  WiFiClient client = modbusServer.accept();
  if (client) {
    Serial.printf("[TCP] Client connected: %s\n",
                  client.remoteIP().toString().c_str());
    client.setTimeout(100);   // 100 ms read timeout

    while (client.connected()) {
      if (client.available()) {
        handleModbusRequest(client);
      }
      updateServo();
    }
    client.stop();
    Serial.println("[TCP] Client disconnected");
  }

  // Also update servo when no client is connected
  updateServo();
}
