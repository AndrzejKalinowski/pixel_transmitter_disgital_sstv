#include <Arduino.h>
#include <FS.h>
#include <TJpg_Decoder.h>
#include "protocol.h"
#include "jpeg.h"

static uint16_t framebuffer[TX_W * TX_H];

// Nearest-neighbor source coordinate for each output pixel, in the
// TJpg-scaled image space. Rebuilt for every decode.
static uint16_t mapX[TX_W];
static uint16_t mapY[TX_H];

static const char* jresultName(JRESULT r) {
  switch (r) {
    case JDR_OK:   return "OK";
    case JDR_INTR: return "interrupted by callback";
    case JDR_INP:  return "file read error";
    case JDR_MEM1: return "workspace too small";
    case JDR_MEM2: return "stream buffer too small";
    case JDR_PAR:  return "parameter error";
    case JDR_FMT1: return "data format error (corrupt or truncated JPEG?)";
    case JDR_FMT2: return "format not supported";
    case JDR_FMT3: return "format not supported (progressive JPEG?)";
    default:       return "unknown";
  }
}

// TJpg_Decoder hands us one decoded MCU block at a time (scaled coords,
// w/h pre-clipped at image edges) — the full bitmap never sits in RAM.
// Paint every framebuffer pixel whose nearest-neighbor source falls inside
// this block.
static bool blockCallback(int16_t bx, int16_t by, uint16_t bw, uint16_t bh, uint16_t* data) {
  for (int fy = 0; fy < TX_H; fy++) {
    int sy = mapY[fy];
    if (sy < by || sy >= by + (int)bh) {
      continue;
    }
    for (int fx = 0; fx < TX_W; fx++) {
      int sx = mapX[fx];
      if (sx < bx || sx >= bx + (int)bw) {
        continue;
      }
      framebuffer[fy * TX_W + fx] = data[(sy - by) * bw + (sx - bx)];
    }
  }
  return true;  // keep decoding
}

bool jpegDecodeToFramebuffer(fs::FS& fs, const String& path) {
  uint16_t nativeW = 0, nativeH = 0;
  JRESULT r = TJpgDec.getFsJpgSize(&nativeW, &nativeH, path, fs);
  if (r != JDR_OK || nativeW == 0 || nativeH == 0) {
    Serial.print(F("JPEG header parse FAILED: "));
    Serial.println(jresultName(r));
    return false;
  }
  Serial.print(F("JPEG native size: "));
  Serial.print(nativeW);
  Serial.print(F(" x "));
  Serial.println(nativeH);

  // Largest TJpg scale divider that still leaves >= TX_W x TX_H to sample
  // from; smaller sources are nearest-neighbor upscaled instead.
  uint8_t scale = 1;
  for (uint8_t s = 8; s >= 2; s /= 2) {
    if (nativeW / s >= TX_W && nativeH / s >= TX_H) {
      scale = s;
      break;
    }
  }
  int scaledW = nativeW / scale;
  int scaledH = nativeH / scale;
  if (scaledW < 1) scaledW = 1;
  if (scaledH < 1) scaledH = 1;
  Serial.print(F("decode scale 1/"));
  Serial.print(scale);
  Serial.print(F(" -> "));
  Serial.print(scaledW);
  Serial.print(F(" x "));
  Serial.print(scaledH);
  Serial.print(F(", resample to "));
  Serial.print(TX_W);
  Serial.print(F(" x "));
  Serial.println(TX_H);

  for (int fx = 0; fx < TX_W; fx++) {
    int sx = (int)((uint32_t)fx * scaledW / TX_W);
    mapX[fx] = (sx < scaledW) ? sx : scaledW - 1;
  }
  for (int fy = 0; fy < TX_H; fy++) {
    int sy = (int)((uint32_t)fy * scaledH / TX_H);
    mapY[fy] = (sy < scaledH) ? sy : scaledH - 1;
  }

  // Mid-gray prefill so any pixel the decoder never delivers (e.g. rounding
  // at the scaled edge) is visibly neutral rather than stale data.
  for (int i = 0; i < TX_W * TX_H; i++) {
    framebuffer[i] = 0x8410;
  }

  TJpgDec.setJpgScale(scale);
  TJpgDec.setSwapBytes(false);
  TJpgDec.setCallback(blockCallback);

  uint32_t t0 = millis();
  r = TJpgDec.drawFsJpg(0, 0, path, fs);  // opens its own fresh handle
  uint32_t elapsed = millis() - t0;

  if (r != JDR_OK) {
    Serial.print(F("JPEG decode FAILED: "));
    Serial.println(jresultName(r));
    return false;
  }

  Serial.print(F("decode OK in "));
  Serial.print(elapsed);
  Serial.print(F(" ms, framebuffer checksum 0x"));
  Serial.println(jpegChecksum(), HEX);
  return true;
}

const uint16_t* jpegFramebuffer() {
  return framebuffer;
}

uint32_t jpegChecksum() {
  uint32_t h = 2166136261u;
  const uint8_t* p = (const uint8_t*)framebuffer;
  for (size_t i = 0; i < sizeof(framebuffer); i++) {
    h ^= p[i];
    h *= 16777619u;
  }
  return h;
}
