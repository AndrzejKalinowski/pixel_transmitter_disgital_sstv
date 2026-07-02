# Pico 2 Digital SSTV Image Transmitter

> [!WARNING]
> **This project is entirely vibe-coded.** All of it — firmware, receiver,
> protocol, this README — was written by an AI coding agent, with a human
> only flashing builds and reporting what the hardware did. It is a hobby
> experiment. **Do not use it. At all.** Not in production, not in anything
> safety- or reliability-relevant, not as a reference for how to do any of
> this properly. It transmits on 433 MHz — make sure you are allowed to do
> that where you live before powering it on.

A one-way image link that works like a *digital* SSTV. Drop a JPEG onto a
Raspberry Pi Pico 2 (it shows up as a USB flash drive), eject the drive, and
the Pico decodes the JPEG, downscales it, and transmits it **tile by tile**
over a CC1101 433 MHz radio as FSK packets. A PC with an RTL-SDR dongle
receives the packets and reassembles the image live, painting each tile as
it arrives. Lost packets just leave a single gray tile — the image degrades
*locally* instead of failing outright, which is the whole point of sending
raw pixels instead of JPEG bytes.

Full hardware wiring, protocol design, and hard-won debugging notes live in
[CLAUDE.md](CLAUDE.md) — read that first if you're changing firmware or RX
code.

## How it works

1. **Firmware** (Pico 2 + CC1101) exposes an on-board flash partition as a
   USB drive. You copy a `.jpg` onto it.
2. **Ejecting the drive** is the transmit trigger. The Pico takes back the
   filesystem, finds the newest JPEG, decodes it (baseline JPEG only),
   downscales it to 128x128 RGB565, and splits it into a 8x8 grid of 16x16
   tiles.
3. Each tile is chunked into small radio packets and sent over CC1101 at
   434.0 MHz / 4.8 kbps, repeated 3x for loss tolerance (no ACKs — this is a
   one-way link).
4. **The receiver** (a PC + RTL-SDR) runs `rtl_433` with a custom flex
   decoder matched to this project's PHY, and a Python script reassembles
   the tiles into `out.png`, filling in gray where tiles were lost.

## Repo layout

```
.
├── CLAUDE.md          # hardware wiring, protocol spec, gotchas — read this first
├── firmware/          # PlatformIO project for the Pico 2 (transmitter)
│   ├── platformio.ini
│   └── src/
│       ├── main.cpp     # setup/loop, eject trigger, orchestration
│       ├── radio.cpp/.h    # CC1101 init + packet TX (RadioLib)
│       ├── jpeg.cpp/.h     # TJpg_Decoder wrapper -> RGB565 framebuffer
│       ├── tiles.cpp/.h    # tiling + packet building + repeat logic
│       ├── usbmsc.cpp/.h   # USB mass storage (FatFS) + eject callback
│       └── protocol.h      # shared packet/image constants (mirrored in rx/)
└── rx/                # Python receiver (RTL-SDR -> out.png)
    ├── run_rtl433.sh / .ps1   # rtl_433 with the documented -X flex spec
    ├── reassemble.py          # JSON in -> de-whiten -> CRC -> paint out.png
    ├── protocol.py            # constants mirrored from firmware protocol.h
    ├── dewhiten.py            # CC1101 PN9 sequence (self-tested vs TI DN509)
    └── requirements.txt
```

## Hardware

- Raspberry Pi Pico 2 (RP2350), arduino-pico (earlephilhower) core
- CC1101 433 MHz module, SMA whip antenna
- RTL-SDR dongle for receiving (no second CC1101 — RX is software-defined)

Full wiring table and known-good pin assignments are in
[CLAUDE.md](CLAUDE.md#hardware-fixed--do-not-change-without-asking-the-user).

## Building & flashing the firmware

Requires [PlatformIO](https://platformio.org/) (CLI or the VSCode extension).

```sh
pio run -d firmware -t upload      # build + flash over USB
pio device monitor -b 115200       # serial console
```

## Using it

1. Plug in the Pico 2. It boots, brings up the radio, and shows up as a USB
   drive.
2. Copy a baseline JPEG onto the drive.
3. Eject the drive (right-click -> Eject, or your OS's safe-remove). This
   triggers the transmission — progress prints over serial (~4 min per
   frame). The drive re-mounts right away so you can stage the next image.
4. On the PC, start the receiver **before ejecting**:

   ```powershell
   cd rx
   pip install -r requirements.txt      # once
   .\run_rtl433.ps1                     # Windows (rtl_433.exe on PATH)
   ./run_rtl433.sh                      # Linux/macOS
   ```

   `out.png` starts as a gray canvas and fills in tile by tile as packets
   arrive; tiles that never make it stay gray. Reception accumulates across
   repeated transmissions of the same image, so ejecting again fills holes.
   Bench tip: the RTL-SDR overloads at desk range — set a low gain
   (`$env:GAIN=1`) or remove the SDR antenna.

## Project status

Built incrementally, milestone by milestone (see the kickoff prompt in
`CLAUDE.md`'s history for the full plan):

- [x] **Milestone 1** — Radio smoke test (CC1101 bring-up, repeated TX over RadioLib)
- [x] **Milestone 2** — USB mass storage + eject trigger
- [x] **Milestone 3** — JPEG decode + downscale to 128x128 RGB565
- [x] **Milestone 4** — Tiling, packet protocol, and transmission
- [x] **Milestone 5** — RTL-SDR receiver (`rx/`) — pipeline verified against
  synthetic captures; awaiting over-the-air validation

This file gets updated as milestones complete — see the section below for
the changelog.

## Changelog

- **2026-07-02** — Milestone 5: RTL-SDR receiver. rtl_433 flex decoder spec
  (FSK_PCM, 208 us/bit, preamble+sync `aad391`) documented field-by-field in
  `rx/run_rtl433.sh`; PN9 de-whitening and CC1101 CRC-16 verification happen
  in Python (rtl_433 sees only whitened bits). Reassembler dedupes chunks,
  paints completed tiles, accumulates across re-transmissions. Verified
  end-to-end with a synthetic capture (dropped/corrupted/duplicated packets
  behave as designed). Firmware: added CC1101 register dump, decode stats,
  and per-tile packet-count/ETA progress lines.
- **2026-07-02** — Milestone 4 done: full tiling + packet protocol per
  `protocol.h` (52-byte payloads, 10 chunks/tile, frame header 5x, every
  packet 3x), ~4 min per frame at 4.8 kbps. Filesystem is handed back to
  the host before transmitting, so the next image can be staged during TX.
- **2026-07-02** — Milestone 3 done: TJpg_Decoder integration, JPEG decode
  with graceful failure on progressive/corrupt files, nearest-neighbor
  resample to 128x128 RGB565, framebuffer checksum for verification.
- **2026-07-02** — Milestone 2 done: USB MSC drive backed by FatFS (not
  LittleFS — see CLAUDE.md's gotcha checklist), eject-triggered file
  discovery.
- **2026-07-02** — Milestone 1 done: CC1101 bring-up over RadioLib, working
  around a GDO0/GDO2 wiring swap on this bench with a MARCSTATE-polling
  `txPacket()` wrapper instead of blocking `transmit()`.
