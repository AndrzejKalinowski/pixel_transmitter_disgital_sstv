// Pico 2 "digital SSTV" image transmitter — see ../CLAUDE.md
//
// Full TX chain:
//   1. Pico exposes its FAT partition as a USB drive; user copies a .jpg on.
//   2. User EJECTS the drive — that's the transmit trigger.
//   3. Firmware takes filesystem ownership, finds the most recent
//      .jpg/.jpeg, decodes it (baseline only) into a 128x128 RGB565
//      framebuffer, releases the filesystem back to the host, then
//      transmits the frame per protocol.h: TX_REPEAT whole-frame passes,
//      each pass = headers + all tile chunks, with progress printed.

#include <Arduino.h>
#include "radio.h"
#include "usbmsc.h"
#include "jpeg.h"
#include "tiles.h"

static void haltBlinking(const char* why) {
  Serial.print(F("FATAL: "));
  Serial.println(why);
  pinMode(LED_BUILTIN, OUTPUT);
  while (true) {
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
    delay(100);
  }
}

void setup() {
  Serial.begin(115200);
  uint32_t t0 = millis();
  while (!Serial && (millis() - t0) < 3000) {}  // wait for USB CDC, but don't require it

  Serial.println();
  Serial.println(F("=== Pico 2 digital-SSTV pixel transmitter ==="));
  Serial.print(F("build " __DATE__ " " __TIME__ ", free heap "));
  Serial.print(rp2040.getFreeHeap());
  Serial.println(F(" bytes"));

  if (!radioSetup()) {
    haltBlinking("radio setup failed (wiring per CLAUDE.md?)");
  }

  usbmscSetup();

  Serial.println(F("ready: copy a .jpg/.jpeg onto the USB drive, then EJECT it"));
}

void loop() {
  static uint32_t lastHeartbeat = 0;
  if (millis() - lastHeartbeat > 15000) {
    lastHeartbeat = millis();
    Serial.println(F("waiting for eject..."));
  }

  if (!usbmscEjectPending()) {
    delay(10);
    return;
  }

  Serial.print(F("["));
  Serial.print(millis());
  Serial.println(F(" ms] EJECT detected — taking filesystem ownership"));
  if (!usbmscTakeOwnership()) {
    return;
  }

  size_t size = 0;
  String name = usbmscFindLatestJpeg(&size);
  bool decoded = false;
  if (name.length() == 0) {
    Serial.println(F("no .jpg/.jpeg found on the drive"));
  } else {
    Serial.print(F("latest JPEG: "));
    Serial.print(name);
    Serial.print(F(" ("));
    Serial.print((unsigned)size);
    Serial.println(F(" bytes)"));
    decoded = jpegDecodeToFramebuffer(usbmscFilesystem(), "/" + name);
  }

  // Hand the drive back before the (minutes-long) transmission: the decoded
  // framebuffer is self-contained, and the host can already stage the next
  // image while this one is on the air.
  usbmscReleaseOwnership();
  Serial.println(F("filesystem released back to host"));

  if (decoded) {
    tilesTransmitFrame(jpegFramebuffer());
    Serial.println(F("eject again (or copy a new image first) to re-transmit"));
  }
}
