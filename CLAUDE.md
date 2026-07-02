# CLAUDE.md — Pico 2 "Digital SSTV" Image Transmitter over CC1101

## What this project is

A one-way image link that works like a *digital* SSTV. A JPEG is copied onto a
Raspberry Pi Pico 2 (which appears to the host PC as a USB flash drive). When the
user ejects the drive, the Pico decodes the JPEG, downscales it to a fixed small
resolution, and transmits it **pixel-tile by pixel-tile** as CC1101 FSK packets.
An RTL-SDR on a PC receives the packets and reassembles them into an image,
painting each tile as it arrives. Lost packets leave a single gray tile — the
image degrades *locally*, never catastrophically. This is the whole point of
sending pixels instead of the raw JPEG bytes: JPEG is fragile to any missing
byte, a tiled pixel stream is not.

This repo has two halves:
- `firmware/` — PlatformIO project for the Pico 2 (transmitter)
- `rx/` — Python receiver that drives an RTL-SDR and rebuilds the image

## Hardware (fixed — do not change without asking the user)

- **MCU:** Raspberry Pi Pico 2 (RP2350). Core: **arduino-pico by Earle Philhower**
  (`earlephilhower`), NOT the Arduino Mbed core and NOT the bare Pico SDK.
- **Radio:** CC1101 433 MHz module (Ebyte E07-M1101D style, "433M V2.0" board),
  SMA whip antenna.
- **Receiver:** RTL-SDR dongle on the PC. **No second CC1101 is available** — the
  receiver MUST be software-defined. Do not propose a second CC1101 as the RX.

### Verified wiring (already known-good — do not re-derive)

Default SPI0 bus is used, so NO `SPI.setRX/setSCK/setTX` calls are needed.

| CC1101 pin | Pico GPIO |
|------------|-----------|
| VCC        | 3V3       |
| GND        | GND       |
| SCK        | GP18      |
| MOSI (SI)  | GP19      |
| MISO (SO)  | GP16      |
| CSN        | GP17      |
| GDO0       | GP20      |
| GDO2       | GP21      |

The CC1101 answers `VERSION = 0x14` over SPI. `SPI.begin()` MUST be called in
`setup()` before `radio.begin()` — omitting it causes RADIOLIB_ERR_CHIP_NOT_FOUND
(-2). This was a real bug; do not remove that call.

## Firmware architecture (`firmware/`)

### Libraries
- **RadioLib** (jgromes) — CC1101 packet TX. Use packet mode only (NOT direct/async
  mode; async analog modes do not work reliably on this chip — proven).
- **TJpg_Decoder** (Bodmer) — decodes **baseline** JPEG in small MCU blocks via a
  callback, so the full bitmap never sits in RAM. Progressive JPEGs are NOT
  supported; if decode fails, report it over serial, do not crash.
- **LittleFS** (built into arduino-pico) — on-chip flash filesystem holding the JPEG.
- **TinyUSB MSC** (built into arduino-pico) — exposes the LittleFS partition as a
  USB mass-storage drive to the host.

### The USB-drive + trigger flow (important, has a real gotcha)
The Pico and the host PC cannot both own the LittleFS filesystem at the same time.
So:
1. On boot, the Pico mounts its LittleFS partition and exposes it as a USB MSC
   drive. The user drags a `.jpg` onto it.
2. **The trigger to transmit is the host EJECTING the drive.** On eject, TinyUSB
   signals the firmware; the Pico then takes ownership of LittleFS, finds the most
   recently modified `*.jpg`/`*.jpeg`, and begins transmission.
3. This "eject to send" is the robust form of "transmit when a file appears."
   Implement the eject callback; do not poll the filesystem while USB has it mounted.

If exposing writable MSC + LittleFS proves too fiddly in arduino-pico, an acceptable
fallback is: mount MSC read/write, and detect "new file" by comparing a stored
hash/mtime after the host writes — but prefer the eject-signal approach first.

### Image processing
- Decode the JPEG with TJpg_Decoder.
- Downscale to a **fixed transmit resolution**: `TX_W = 128`, `TX_H = 128`.
- Color format: **RGB565** (2 bytes/pixel). (A `GRAYSCALE` compile flag that packs
  1 byte/pixel is a nice-to-have; leave a `#define COLOR_MODE` hook but default RGB565.)
- TJpg_Decoder can scale by 1/2, 1/4, 1/8 during decode — use its scaling to get
  close to 128×128, then finish with simple nearest-neighbor if needed.

### Tiling + packet protocol (the core of the "digital SSTV" idea)
- The 128×128 image is divided into **16×16-pixel tiles** → an 8×8 grid = **64 tiles**,
  tile index 0..63 in row-major order.
- One 16×16 RGB565 tile = 512 bytes of pixel data. CC1101 packets are small
  (payload kept ≤ 60 bytes to stay well under the 64-byte FIFO with header room),
  so each tile is split into **chunks**.
- **Packet layout** (bytes, little-endian where multi-byte):

  | field        | size | meaning                                  |
  |--------------|------|------------------------------------------|
  | magic        | 1    | 0xA5, identifies our packets             |
  | tile_index   | 1    | 0..63                                    |
  | chunk_index  | 1    | which chunk of this tile                 |
  | chunk_count  | 1    | total chunks per tile (constant)         |
  | payload_len  | 1    | bytes of pixel data in this packet       |
  | payload      | ≤55  | RGB565 pixel bytes                       |

  RadioLib appends its own CRC (enable CRC), so we do not add our own CRC field —
  but DO enable RadioLib's CRC so corrupt packets are dropped, not painted.

- A **frame header packet** (tile_index = 0xFF sentinel) is sent first, carrying:
  magic, 0xFF, TX_W, TX_H, tile_pixels (16), color_mode, total_tiles. This lets the
  RX size its canvas without hardcoding. Send it 5× at the start.

### Redundancy (one-way link, no ACKs)
- Send **every packet 3×** (configurable `#define TX_REPEAT 3`). The RX dedupes by
  (tile_index, chunk_index). This trades airtime for loss tolerance. Missing chunks
  → that tile stays gray on the RX.

### CC1101 radio config (RadioLib, packet mode)
- Frequency: **434.0 MHz**
- Bit rate: **4.8 kbps** (slow + reliable; user confirmed slow is fine)
- 2-FSK, frequency deviation ~5 kHz, RX bandwidth default
- Sync word: pick a distinctive 2-byte sync (e.g. 0xD3 0x91) and DOCUMENT it — the
  RX flex-decoder needs to match it exactly.
- Data whitening: **ON** (note: the RX must de-whiten identically — document the
  whitening polynomial RadioLib/CC1101 uses, PN9).
- CRC: **ON**.
- Preamble length: ≥ 4 bytes (longer helps the SDR lock — consider 8).
- Output power: start low (`setOutputPower(0)` or lower) for benchtop; the SDR
  overloads easily at close range — the user should also drop RTL-SDR gain and
  remove the SDR antenna on the bench.

### Serial diagnostics
- 115200 baud. On boot print VERSION and MARCSTATE. On trigger print filename, decoded
  dimensions, tile count, packet count, and a progress line per tile. This mirrors the
  debugging style that got the hardware working; keep it verbose.

## Receiver architecture (`rx/`)

### Primary path: rtl_433 flex decoder → Python reassembler
- **Reality check for the agent:** `rtl_433` decodes *known* protocols out of the box.
  To decode our CUSTOM packets we must use its **flex decoder** (`-X` spec string)
  configured to match our exact PHY: modulation (FSK_PCM), bitrate (4.8 kbps →
  ~208 µs/bit), preamble/sync bits (our sync word), and packet length. The agent MUST
  construct and document this `-X` string; do not assume rtl_433 auto-detects our format.
- Run `rtl_433` outputting JSON lines (`-F json`), each carrying the raw payload bytes.
  A Python script (`rx/reassemble.py`) reads that JSON stream, parses our packet layout,
  dedupes by (tile_index, chunk_index), and paints tiles onto a canvas.
- **De-whitening:** if rtl_433's flex decoder delivers still-whitened bytes, the Python
  side must apply the CC1101 PN9 de-whitening before interpreting fields. Document
  clearly whether de-whitening happens in rtl_433 or in Python — this is a common
  source of "garbage bytes."

### Canvas / output
- Parse the frame-header packet to get TX_W, TX_H, tile size, color_mode, total_tiles.
- Allocate an RGB canvas, fill with mid-gray.
- For each fully-received tile (all chunks present), convert RGB565→RGB888 and blit it
  at its grid position.
- Missing tiles remain gray → visible, localized loss (the digital-SSTV behavior).
- Save incrementally to `out.png` and optionally live-display with OpenCV/matplotlib so
  the user watches the image build up like real SSTV.

### Fallback path (appendix, only if flex decoder can't cope)
- If rtl_433's flex decoder cannot handle variable length + whitening + our sync, fall
  back to a GNU Radio (or pure Python via `pyrtlsdr` + numpy) FSK demodulator:
  quadrature-demod → clock recovery → correlate sync word → slice bytes → de-whiten →
  parse. This is significantly more work; only go here if the rtl_433 path is exhausted.
  Document why, if you switch.

## Repo layout
```
.
├── CLAUDE.md
├── firmware/
│   ├── platformio.ini
│   ├── src/
│   │   ├── main.cpp          # setup/loop, eject trigger, orchestration
│   │   ├── radio.cpp/.h      # CC1101 init + packet TX (RadioLib)
│   │   ├── jpeg.cpp/.h        # TJpg_Decoder wrapper -> RGB565 framebuffer/tiles
│   │   ├── tiles.cpp/.h       # tiling + packet building + repeat logic
│   │   ├── usbmsc.cpp/.h      # TinyUSB MSC + LittleFS + eject callback
│   │   └── protocol.h         # shared packet struct/constants (mirrored in rx/)
│   └── data/                  # (optional) default test image for LittleFS
└── rx/
    ├── requirements.txt       # pyrtlsdr / numpy / pillow / opencv-python (as needed)
    ├── run_rtl433.sh          # rtl_433 invocation with the documented -X flex string
    ├── reassemble.py          # reads rtl_433 JSON -> paints canvas -> out.png
    ├── protocol.py            # packet layout constants mirrored from protocol.h
    └── dewhiten.py            # CC1101 PN9 de-whitening (if needed)
```

## platformio.ini (starting point)
```ini
[env:rpipico2]
platform = https://github.com/maxgerhardt/platform-raspberrypi.git
board = rpipico2
framework = arduino
board_build.core = earlephilhower
board_build.filesystem_size = 1m         ; LittleFS partition exposed over USB
monitor_speed = 115200
lib_deps =
    jgromes/RadioLib
    bodmer/TJpg_Decoder
build_flags =
    -DUSE_TINYUSB                        ; required for TinyUSB MSC on arduino-pico
```
Note: the agent should verify the correct board id (`rpipico2`) and the current way
arduino-pico enables TinyUSB MSC; APIs shift between core versions. Check the installed
core version and adapt rather than trusting these flags blindly.

## Constraints & non-goals
- Do NOT use CC1101 async/direct mode or attempt analog SSTV — proven not to work on
  this chip. Packet mode only.
- Do NOT propose a second CC1101 receiver. RX is RTL-SDR only.
- Do NOT hold the whole decoded bitmap AND the JPEG in RAM simultaneously if it risks
  OOM — stream tiles from the decoder. The RP2350 has 520 KB SRAM; 128×128 RGB565 is
  32 KB, which is fine, but keep an eye on it if resolution grows.
- Keep TX resolution/tile size in `protocol.h` as constants so firmware and RX agree.
- Baseline JPEG only.

## Definition of done (v1)
1. Copy a JPEG to the Pico USB drive, eject it.
2. Pico decodes, prints progress over serial, transmits 64 tiles ×3.
3. `rx/reassemble.py` (fed by rtl_433) builds `out.png` showing the image, with any
   dropped tiles as gray squares.
4. Re-running the transmission fills in more tiles (RX can accumulate across repeats).

## Known-gotcha checklist (learned the hard way — honor these)
- `SPI.begin()` before `radio.begin()`, always.
- Default SPI0 pins (GP16/18/19) → no pin-remap calls.
- RTL-SDR overloads at bench range: low gain, antenna off, center exactly on carrier,
  NFM not WFM (for any manual demod).
- rtl_433 will NOT auto-know our packet format: the `-X` flex string is mandatory and
  must match bitrate + sync + length.
- Whitening must be de-whitened on exactly one side; mismatch = garbage bytes.
- Enable RadioLib CRC so bad packets are dropped, not painted as noise tiles.
- Do NOT use blocking `radio.transmit()` — it always returns -5 (TX_TIMEOUT) on this
  bench. RadioLib 7.7.1 waits for the chip's GDO2 end-of-packet pulse on GP21, but
  here that pulse arrives on GP20 (GDO0/GDO2 jumpers swapped at one end; confirmed
  by the Milestone-1 wire-map test; user chose to keep the wiring as-is). Use the
  `txPacket()` wrapper in `radio.cpp`: `startTransmit()` + poll MARCSTATE (0x35) until
  IDLE + `finishTransmit()`. Works with no GDO wiring at all; ~36 ms per packet.
- The USB-shared partition is FatFS, NOT LittleFS: MSC exposes raw blocks and Windows
  can only mount FAT. Use the core's `FatFS` + `FatFSUSB` libraries (same `FS` API as
  LittleFS on the firmware side; `onUnplug` callback = the eject trigger).
- Do NOT add `-DUSE_TINYUSB`: FatFSUSB `#error`s under Adafruit TinyUSB — it uses the
  core's native USB stack and registers MSC alongside CDC serial, so the same COM port
  keeps working while the drive is mounted.
