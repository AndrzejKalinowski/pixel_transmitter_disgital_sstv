#pragma once
#include <stdint.h>

// Transmit one full frame from the decoded TX_W x TX_H RGB565 framebuffer:
// frame-header packet HEADER_REPEAT times, then all TOTAL_TILES tiles,
// each chunk TX_REPEAT times, with a progress line per tile. Blocking
// (roughly 4 minutes at 4.8 kbps with TX_REPEAT=3); TX failures are
// counted and reported, not fatal.
void tilesTransmitFrame(const uint16_t* framebuffer);
