#pragma once
#include <stdint.h>

// Transmit one full frame from the decoded TX_W x TX_H RGB565 framebuffer:
// frame-header packet HEADER_REPEAT times, then all TOTAL_TILES tiles,
// each chunk TX_REPEAT times, with a progress line per tile. Blocking
// (~2 minutes at 9.6 kbps with TX_REPEAT=3 — it prints its own estimate);
// TX failures are counted and reported, not fatal.
void tilesTransmitFrame(const uint16_t* framebuffer);
