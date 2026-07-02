#include <Arduino.h>
#include "protocol.h"
#include "radio.h"
#include "tiles.h"

// CC1101 on-air framing overhead per packet, for the airtime estimate:
// 8 preamble + 2 sync + 1 length + 2 CRC.
static const uint8_t AIR_OVERHEAD_BYTES = 13;
static const uint8_t PKT_HEADER_BYTES = 5;

static uint32_t packetAirMs(uint8_t appBytes) {
  return ((uint32_t)(AIR_OVERHEAD_BYTES + appBytes) * 8 * 1000) / PHY_BITRATE_BPS;
}

void tilesTransmitFrame(const uint16_t* framebuffer) {
  const uint8_t lastChunkLen = TILE_BYTES - (uint16_t)(CHUNKS_PER_TILE - 1) * PKT_PAYLOAD_MAX;
  const uint32_t packetsPerPass =
      HEADER_REPEAT + (uint32_t)TOTAL_TILES * CHUNKS_PER_TILE;
  const uint32_t totalPackets = packetsPerPass * TX_REPEAT;
  const uint32_t estMs =
      (uint32_t)TX_REPEAT *
          (HEADER_REPEAT * packetAirMs(FRAME_HEADER_BYTES) +
           (uint32_t)TOTAL_TILES *
               ((CHUNKS_PER_TILE - 1) * packetAirMs(PKT_HEADER_BYTES + PKT_PAYLOAD_MAX) +
                packetAirMs(PKT_HEADER_BYTES + lastChunkLen))) +
      totalPackets * PKT_GAP_MS;

  Serial.print(F("transmitting frame: "));
  Serial.print(TX_REPEAT);
  Serial.print(F(" passes x ("));
  Serial.print(TOTAL_TILES);
  Serial.print(F(" tiles x "));
  Serial.print(CHUNKS_PER_TILE);
  Serial.print(F(" chunks + "));
  Serial.print(HEADER_REPEAT);
  Serial.print(F(" headers) = "));
  Serial.print(totalPackets);
  Serial.print(F(" packets, est "));
  Serial.print(estMs / 1000);
  Serial.println(F(" s"));

  uint32_t t0 = millis();
  uint32_t fails = 0;
  uint32_t packetsSent = 0;

  uint8_t pkt[PKT_HEADER_BYTES + PKT_PAYLOAD_MAX];
  uint8_t tileBytes[TILE_BYTES];

  // Whole-frame passes: every packet still goes out TX_REPEAT times in
  // total, but copies of the same chunk are minutes apart, so bursty loss
  // (or a receiver hiccup) can't eat all of them at once.
  for (uint8_t pass = 0; pass < TX_REPEAT; pass++) {
    // Frame header before each pass, so even a receiver that joins late
    // can size its canvas.
    pkt[0] = PKT_MAGIC;
    pkt[1] = TILE_INDEX_HEADER;
    pkt[2] = TX_W & 0xFF;
    pkt[3] = TX_W >> 8;
    pkt[4] = TX_H & 0xFF;
    pkt[5] = TX_H >> 8;
    pkt[6] = TILE_PIXELS;
    pkt[7] = COLOR_MODE_RGB565;
    pkt[8] = TOTAL_TILES;
    for (uint8_t i = 0; i < HEADER_REPEAT; i++) {
      if (txPacket(pkt, FRAME_HEADER_BYTES) != 0) {  // 0 = RADIOLIB_ERR_NONE
        fails++;
      }
      packetsSent++;
      delay(PKT_GAP_MS);
    }
    Serial.print(F("pass "));
    Serial.print(pass + 1);
    Serial.print(F("/"));
    Serial.print(TX_REPEAT);
    Serial.println(F(": header sent"));

    for (uint8_t tile = 0; tile < TOTAL_TILES; tile++) {
      // Extract the tile, row-major, RGB565 little-endian per pixel.
      const uint16_t x0 = (tile % TILES_X) * TILE_PIXELS;
      const uint16_t y0 = (tile / TILES_X) * TILE_PIXELS;
      uint16_t idx = 0;
      for (uint8_t row = 0; row < TILE_PIXELS; row++) {
        const uint16_t* src = &framebuffer[(y0 + row) * TX_W + x0];
        for (uint8_t col = 0; col < TILE_PIXELS; col++) {
          tileBytes[idx++] = src[col] & 0xFF;
          tileBytes[idx++] = src[col] >> 8;
        }
      }

      for (uint8_t chunk = 0; chunk < CHUNKS_PER_TILE; chunk++) {
        const uint16_t offset = (uint16_t)chunk * PKT_PAYLOAD_MAX;
        const uint8_t len =
            (offset + PKT_PAYLOAD_MAX <= TILE_BYTES) ? PKT_PAYLOAD_MAX : (TILE_BYTES - offset);
        pkt[0] = PKT_MAGIC;
        pkt[1] = tile;
        pkt[2] = chunk;
        pkt[3] = CHUNKS_PER_TILE;
        pkt[4] = len;
        memcpy(&pkt[PKT_HEADER_BYTES], &tileBytes[offset], len);

        int16_t state = txPacket(pkt, PKT_HEADER_BYTES + len);
        if (state != 0) {  // 0 = RADIOLIB_ERR_NONE
          fails++;
          Serial.print(F("  TX fail code "));
          Serial.print(state);
          Serial.print(F(" (tile "));
          Serial.print(tile);
          Serial.print(F(" chunk "));
          Serial.print(chunk);
          Serial.println(F(")"));
        }
        packetsSent++;
        delay(PKT_GAP_MS);
      }

      if ((tile + 1) % 8 == 0 || tile + 1 == TOTAL_TILES) {
        const uint32_t elapsed = millis() - t0;
        const uint32_t etaS =
            (uint32_t)(((uint64_t)elapsed * (totalPackets - packetsSent)) / packetsSent / 1000);
        Serial.printf("pass %u/%u tile %2u/%u | %4lu/%lu pkts | %3lu s | ~%3lu s left | %lu fails\r\n",
                      pass + 1, TX_REPEAT, tile + 1, TOTAL_TILES,
                      (unsigned long)packetsSent, (unsigned long)totalPackets,
                      (unsigned long)(elapsed / 1000), (unsigned long)etaS,
                      (unsigned long)fails);
      }
    }
  }

  Serial.print(F("frame complete: "));
  Serial.print(totalPackets);
  Serial.print(F(" packets in "));
  Serial.print((millis() - t0) / 1000);
  Serial.print(F(" s, "));
  Serial.print(fails);
  Serial.println(F(" TX failures"));
}
