// Shared image/packet constants — MUST stay mirrored in rx/protocol.py.
#pragma once
#include <stdint.h>

// ==== PHY (radio.cpp configures the chip from this; rx mirrors it) ====
// 9.6 kbps since 2026-07-03 (2x speedup on user request; deviation scaled
// with it to keep the proven modulation index). Was 4.8 kbps / 5 kHz.
static const uint32_t PHY_BITRATE_BPS = 9600;
static const uint32_t PHY_DEVIATION_HZ = 10000;

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

// ==== Packet protocol ====
// Every packet rides CC1101 variable-length packet mode: the chip's length
// byte precedes the bytes below (RadioLib adds it), CRC-16 appended by the
// chip, PN9 whitening on. PHY: 434.0 MHz, 4.8 kbps 2-FSK, dev 5 kHz,
// preamble 8 bytes, sync word 0xD3 0x91.
//
// Tile-data packet (5-byte header + payload):
//   offset size  meaning
//   0      1     PKT_MAGIC (0xA5)
//   1      1     tile_index, 0..TOTAL_TILES-1, row-major
//   2      1     chunk_index, 0..CHUNKS_PER_TILE-1
//   3      1     chunk_count (always CHUNKS_PER_TILE)
//   4      1     payload_len (bytes of pixel data following)
//   5      ..    payload: RGB565 pixels, low byte first, row-major within
//                the tile, chunk offset = chunk_index * PKT_PAYLOAD_MAX
//
// Frame-header packet (tile_index sentinel 0xFF), sent HEADER_REPEAT times
// before the tiles so the RX can size its canvas:
//   offset size  meaning
//   0      1     PKT_MAGIC
//   1      1     TILE_INDEX_HEADER (0xFF)
//   2      2     TX_W, little-endian
//   4      2     TX_H, little-endian
//   6      1     TILE_PIXELS
//   7      1     color mode (COLOR_MODE_RGB565)
//   8      1     TOTAL_TILES
static const uint8_t  PKT_MAGIC = 0xA5;
static const uint8_t  TILE_INDEX_HEADER = 0xFF;
static const uint8_t  PKT_PAYLOAD_MAX = 52;   // <= 55 per spec; 52 = 26 whole pixels
static const uint16_t TILE_BYTES = (uint16_t)TILE_PIXELS * TILE_PIXELS * 2;                 // 512
static const uint8_t  CHUNKS_PER_TILE = (TILE_BYTES + PKT_PAYLOAD_MAX - 1) / PKT_PAYLOAD_MAX; // 10
static const uint8_t  FRAME_HEADER_BYTES = 9;

// ==== Redundancy (one-way link, no ACKs) ====
static const uint8_t  TX_REPEAT = 3;      // each tile packet sent this many times
static const uint8_t  HEADER_REPEAT = 5;  // frame-header packet repeats
static const uint16_t PKT_GAP_MS = 5;     // breather between packets for the SDR
                                          // (burst separation for the RX demod)
