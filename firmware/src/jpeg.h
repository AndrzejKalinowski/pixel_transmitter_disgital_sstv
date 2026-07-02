#pragma once
#include <Arduino.h>
#include <FS.h>

// Decode a baseline JPEG (full path, e.g. "/photo.jpg") from the given
// filesystem into the fixed TX_W x TX_H RGB565 framebuffer. Uses
// TJpg_Decoder's 1/2/4/8 hardware scaling to get close, then
// nearest-neighbor resampling to hit the exact size (non-square images are
// stretched to fit). Progressive or corrupt JPEGs fail gracefully with a
// serial diagnostic; nothing crashes.
// NOTE: takes fs + path rather than an open fs::File because TJpg_Decoder
// closes any file handle it is given (and fs::File copies share one
// underlying handle) — each library call must open its own.
bool jpegDecodeToFramebuffer(fs::FS& fs, const String& path);

// The decoded TX_W x TX_H RGB565 image (valid after a successful decode).
const uint16_t* jpegFramebuffer();

// FNV-1a over the framebuffer bytes — stable for identical input images,
// used as the Milestone 3 success check.
uint32_t jpegChecksum();
