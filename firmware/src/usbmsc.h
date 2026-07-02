#pragma once
#include <Arduino.h>
#include <FS.h>

// Format-check the on-flash FAT partition, then expose it to the host as a
// USB mass-storage drive (alongside the existing CDC serial). Prints the
// partition info and any files already present.
//
// NOTE (differs from CLAUDE.md's original sketch): the shared partition is
// FatFS, not LittleFS — a USB MSC drive exposes raw blocks and Windows can
// only mount FAT. The core's FatFSUSB library provides exactly the
// eject-signal flow CLAUDE.md asks for, but it is INCOMPATIBLE with
// -DUSE_TINYUSB (it uses the core's native USB stack), so that build flag
// must stay out of platformio.ini.
void usbmscSetup();

// True once after the host ejects the drive — this is the transmit trigger.
// Reading it clears the flag. (Set from the USB interrupt context.)
bool usbmscEjectPending();

// Mount FatFS for firmware use after an eject. While ownership is held the
// drive reports "not ready" to the host so both sides never touch the
// filesystem at once. Returns false if the mount fails.
bool usbmscTakeOwnership();

// Unmount and hand the drive back to the host (it can re-mount on re-plug,
// or immediately if the OS re-probes).
void usbmscReleaseOwnership();

// Find the most recently modified-or-created *.jpg / *.jpeg in the FS root
// (Explorer copies preserve the source mtime but set a fresh creation time,
// so the newer of the two timestamps is used). Skips directories and
// hidden/temp entries. Requires ownership. Returns "" if none found;
// otherwise the name, with size in *sizeOut.
String usbmscFindLatestJpeg(size_t* sizeOut);

// The filesystem holding the user's files (valid while ownership is held).
// Handed out as fs::FS so consumers (e.g. TJpg_Decoder) stay FS-agnostic.
fs::FS& usbmscFilesystem();
