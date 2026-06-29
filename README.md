# ESP32_Network-Security
Implemented TLS to secure Modbus TCP communication between the Python client and ESP32 using self-signed X.509 certificates. The client verifies the ESP32's certificate before establishing an encrypted connection, ensuring the confidentiality and integrity of Modbus commands used to remotely control the servo motor over Wi-Fi.
