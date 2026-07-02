// Shared image/packet constants — MUST stay mirrored in rx/protocol.py.
#pragma once
#include <stdint.h>

// ==== Transmit image geometry ====
static const uint16_t TX_W = 128;
static const uint16_t TX_H = 128;
static const uint8_t  TILE_PIXELS = 16;                  // 16x16-pixel tiles
static const uint8_t  TILES_X = TX_W / TILE_PIXELS;      // 8
static const uint8_t  TILES_Y = TX_H / TILE_PIXELS;      // 8
static const uint8_t  TOTAL_TILES = TILES_X * TILES_Y;   // 64

// ==== Color mode ====
// RGB565, 2 bytes per pixel, little-endian on air.
static const uint8_t COLOR_MODE_RGB565 = 0;

// (Packet layout constants — magic, chunking, repeats — arrive in Milestone 4.)
