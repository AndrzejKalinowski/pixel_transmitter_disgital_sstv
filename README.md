# Pico 2 Digital SSTV Image Transmitter

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
└── rx/                # Python receiver (RTL-SDR -> out.png), added in Milestone 5
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
   triggers the transmission — progress prints over serial.
4. On the PC, run the RTL-SDR receiver (see `rx/`, once Milestone 5 lands)
   to watch the image build up in `out.png`.

## Project status

Built incrementally, milestone by milestone (see the kickoff prompt in
`CLAUDE.md`'s history for the full plan):

- [x] **Milestone 1** — Radio smoke test (CC1101 bring-up, repeated TX over RadioLib)
- [x] **Milestone 2** — USB mass storage + eject trigger
- [x] **Milestone 3** — JPEG decode + downscale to 128x128 RGB565
- [x] **Milestone 4** — Tiling, packet protocol, and transmission
- [ ] **Milestone 5** — RTL-SDR receiver (`rx/`)

This file gets updated as milestones complete — see the section below for
the changelog.

## Changelog

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
