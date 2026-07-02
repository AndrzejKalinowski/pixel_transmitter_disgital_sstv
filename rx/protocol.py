"""Packet/image constants — MUST stay mirrored with firmware/src/protocol.h.

Tile-data packet (after de-whitening, after the CC1101 length byte):
  offset size  meaning
  0      1     PKT_MAGIC (0xA5)
  1      1     tile_index, 0..TOTAL_TILES-1, row-major (0xFF = frame header)
  2      1     chunk_index, 0..CHUNKS_PER_TILE-1
  3      1     chunk_count (always CHUNKS_PER_TILE)
  4      1     payload_len
  5      ..    payload: RGB565 pixels, low byte first, row-major within the
               tile; byte offset within tile = chunk_index * PKT_PAYLOAD_MAX

Frame-header packet body (tile_index == 0xFF):
  0      1     PKT_MAGIC
  1      1     0xFF
  2      2     TX_W, little-endian
  4      2     TX_H, little-endian
  6      1     TILE_PIXELS
  7      1     color mode (COLOR_MODE_RGB565)
  8      1     TOTAL_TILES
"""

TX_W = 128
TX_H = 128
TILE_PIXELS = 16
TILES_X = TX_W // TILE_PIXELS
TILES_Y = TX_H // TILE_PIXELS
TOTAL_TILES = TILES_X * TILES_Y

COLOR_MODE_RGB565 = 0

PKT_MAGIC = 0xA5
TILE_INDEX_HEADER = 0xFF
PKT_PAYLOAD_MAX = 52
TILE_BYTES = TILE_PIXELS * TILE_PIXELS * 2
CHUNKS_PER_TILE = (TILE_BYTES + PKT_PAYLOAD_MAX - 1) // PKT_PAYLOAD_MAX
FRAME_HEADER_BYTES = 9

# PHY (mirrors PHY_* in firmware protocol.h)
SYNC_WORD = bytes([0xD3, 0x91])
BIT_RATE_BPS = 9600     # was 4800 until 2026-07-03
DEVIATION_HZ = 10000


def crc16_cc1101(data: bytes) -> int:
    """CC1101 packet CRC: poly 0x8005, init 0xFFFF, MSB-first, no reflection,
    no final XOR (TI design note DN502). Covers length byte + packet body."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x8005) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc
