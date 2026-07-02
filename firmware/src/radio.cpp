#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>
#include "radio.h"

// Verified wiring (CLAUDE.md): default SPI0 — SCK=GP18, MOSI=GP19, MISO=GP16 —
// so no SPI pin-remap calls. Note the GDO pins below name the Pico GPIOs per
// the wiring table; physically the module's GDO0/GDO2 pads arrive swapped
// (chip GDO2 -> GP20), which is why txPacket() ignores GDO pins entirely.
static const uint8_t PIN_CSN  = 17;
static const uint8_t PIN_GDO0 = 20;
static const uint8_t PIN_GDO2 = 21;
static const uint8_t PIN_MISO = 16;

static CC1101 radio = new Module(PIN_CSN, PIN_GDO0, RADIOLIB_NC, PIN_GDO2);

// Read a CC1101 register directly. The 0xC0 header (read+burst) selects the
// status-register page for addresses 0x30+, and does a plain burst read for
// config registers below 0x30 — so this works for both. Safe between RadioLib
// operations: same SPI settings, and RadioLib leaves CSN high when idle.
static uint8_t cc1101ReadStatusReg(uint8_t addr) {
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_CSN, LOW);
  uint32_t t0 = millis();
  while (digitalRead(PIN_MISO) && (millis() - t0) < 10) {}  // chip-ready = MISO low
  SPI.transfer(addr | 0xC0);
  uint8_t value = SPI.transfer(0x00);
  digitalWrite(PIN_CSN, HIGH);
  SPI.endTransaction();
  return value;
}

bool radioSetup() {
  // MUST come before radio.begin() — omitting it caused a real
  // RADIOLIB_ERR_CHIP_NOT_FOUND (-2) bug on this hardware.
  SPI.begin();

  Serial.print(F("radio.begin(434.0 MHz, 4.8 kbps, dev 5.0 kHz, rxBW 135 kHz, 0 dBm, 64-bit preamble) ... "));
  int16_t state = radio.begin(434.0, 4.8, 5.0, 135.0, 0, 64);
  if (state != RADIOLIB_ERR_NONE) {
    Serial.print(F("FAILED, code "));
    Serial.println(state);
    return false;
  }
  Serial.println(F("OK"));

  Serial.print(F("VERSION   = 0x"));
  Serial.print(radio.getChipVersion(), HEX);
  Serial.println(F("  (expect 0x14)"));
  Serial.print(F("MARCSTATE = 0x"));
  Serial.print(cc1101ReadStatusReg(0x35), HEX);
  Serial.println(F("  (expect 0x01 = IDLE)"));

  state = radio.setSyncWord(0xD3, 0x91);
  if (state != RADIOLIB_ERR_NONE) {
    Serial.print(F("setSyncWord FAILED, code "));
    Serial.println(state);
    return false;
  }
  state = radio.setCrcFiltering(true);
  if (state != RADIOLIB_ERR_NONE) {
    Serial.print(F("setCrcFiltering FAILED, code "));
    Serial.println(state);
    return false;
  }
  state = radio.setEncoding(RADIOLIB_ENCODING_WHITENING);
  if (state != RADIOLIB_ERR_NONE) {
    Serial.print(F("setEncoding(whitening) FAILED, code "));
    Serial.println(state);
    return false;
  }

  Serial.println(F("sync word 0xD3 0x91, CRC on, PN9 whitening on"));
  radioDumpRegisters();
  return true;
}

void radioDumpRegisters() {
  Serial.println(F("CC1101 config registers:"));
  for (uint8_t base = 0x00; base <= 0x2E; base += 8) {
    Serial.print(F("  0x"));
    if (base < 0x10) Serial.print('0');
    Serial.print(base, HEX);
    Serial.print(F(":"));
    for (uint8_t i = 0; i < 8 && base + i <= 0x2E; i++) {
      uint8_t v = cc1101ReadStatusReg(base + i);
      Serial.print(v < 0x10 ? F(" 0") : F(" "));
      Serial.print(v, HEX);
    }
    Serial.println();
  }
  uint8_t pktctrl0 = cc1101ReadStatusReg(0x08);
  Serial.print(F("  PKTCTRL0=0x"));
  Serial.print(pktctrl0, HEX);
  Serial.print(F(" -> whitening "));
  Serial.print((pktctrl0 & 0x40) ? F("ON") : F("OFF"));
  Serial.print(F(", CRC "));
  Serial.print((pktctrl0 & 0x04) ? F("ON") : F("OFF"));
  Serial.print(F(", length mode "));
  Serial.println(pktctrl0 & 0x03);  // 1 = variable (length byte first)
}

int16_t txPacket(const uint8_t* data, size_t len) {
  int16_t state = radio.startTransmit(const_cast<uint8_t*>(data), len);
  if (state != RADIOLIB_ERR_NONE) {
    return state;
  }

  uint8_t marc;
  uint32_t t0 = millis();
  do {
    marc = cc1101ReadStatusReg(0x35);         // MARCSTATE
    if (marc == 0x16) {                       // TXFIFO_UNDERFLOW
      radio.finishTransmit();
      return RADIOLIB_ERR_TX_TIMEOUT;
    }
    if (millis() - t0 > 2000) {
      radio.finishTransmit();
      return RADIOLIB_ERR_TX_TIMEOUT;
    }
  } while (marc != 0x01);                     // until IDLE (TXOFF_MODE=IDLE)

  return radio.finishTransmit();
}
