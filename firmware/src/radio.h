#pragma once
#include <stdint.h>
#include <stddef.h>

// Bring up SPI + CC1101 in packet mode and print boot diagnostics
// (VERSION, MARCSTATE). PHY: 434.0 MHz, 4.8 kbps 2-FSK, 5 kHz deviation,
// 8-byte preamble, sync 0xD3 0x91, CRC on, PN9 whitening on, 0 dBm.
// The RTL-SDR flex decoder (Milestone 5) must match these exactly.
// Returns false after printing details if the radio doesn't come up.
bool radioSetup();

// Transmit one packet and wait for completion by polling MARCSTATE.
// NEVER use blocking radio.transmit() on this hardware — the GDO0/GDO2
// jumpers are swapped, so RadioLib 7.7.1 waits on the wrong pin and always
// returns -5 (see CLAUDE.md known-gotcha checklist). Valid for packets that
// fit the 64-byte TX FIFO in one fill (always true for this protocol).
// Returns a RadioLib status code (0 = RADIOLIB_ERR_NONE).
int16_t txPacket(const uint8_t* data, size_t len);
