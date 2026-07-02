#pragma once
#include <stdint.h>

// Transmit one full frame from the decoded TX_W x TX_H RGB565 framebuffer
// as TX_REPEAT whole-frame passes (headers, then every tile chunk once per
// pass), with progress lines. Blocking (~4 minutes at 4.8 kbps with
// TX_REPEAT=3 — it prints its own estimate); TX failures are counted and
// reported, not fatal.
void tilesTransmitFrame(const uint16_t* framebuffer);
