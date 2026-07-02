// Pico 2 "digital SSTV" image transmitter — see ../CLAUDE.md
//
// Milestone 3: JPEG decode + downscale.
//   1. Pico exposes its FAT partition as a USB drive; user copies a .jpg on.
//   2. User EJECTS the drive — that's the transmit trigger.
//   3. Firmware takes filesystem ownership, finds the most recent
//      .jpg/.jpeg, decodes it (baseline only) into a 128x128 RGB565
//      framebuffer, and prints dimensions + a stable checksum.
// (Tiling + packet TX is Milestone 4.)
//
// The radio is brought up at boot exactly as in Milestone 1 so every boot
// re-verifies the SPI/CC1101 path; nothing is transmitted yet.

#include <Arduino.h>
#include "radio.h"
#include "usbmsc.h"
#include "jpeg.h"

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
  Serial.println(F("=== Pico image TX — Milestone 3: JPEG decode + downscale ==="));

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

  Serial.println(F("EJECT detected — taking filesystem ownership"));
  if (!usbmscTakeOwnership()) {
    return;
  }

  size_t size = 0;
  String name = usbmscFindLatestJpeg(&size);
  if (name.length() == 0) {
    Serial.println(F("no .jpg/.jpeg found on the drive"));
  } else {
    Serial.print(F("latest JPEG: "));
    Serial.print(name);
    Serial.print(F(" ("));
    Serial.print((unsigned)size);
    Serial.println(F(" bytes)"));

    bool ok = jpegDecodeToFramebuffer(usbmscFilesystem(), "/" + name);
    if (ok) {
      // Milestone 4 hook: tile + transmit the framebuffer here.
    }
  }

  usbmscReleaseOwnership();
  Serial.println(F("filesystem released — re-plug the drive to copy another file"));
}
