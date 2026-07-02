#include <FatFS.h>
#include <FatFSUSB.h>
#include "usbmsc.h"

static volatile bool ejectFlag = false;
static volatile bool fsOwned = false;

// Called from the USB stack when the host ejects the drive (SCSI
// START_STOP_UNIT). Interrupt context — just flag it, work happens in loop().
static void onUnplugCb(uint32_t) {
  ejectFlag = true;
}

static void onPlugCb(uint32_t) {
  // Host (re)attached the medium; nothing to do — ownership arbitration is
  // handled by driveReadyCb.
}

// Host polls Test Unit Ready; while firmware owns the filesystem the drive
// reports not-ready so the host won't issue reads/writes mid-transmission.
static bool driveReadyCb(uint32_t) {
  return !fsOwned;
}

// Print partition stats and every root entry — shared boot/eject diagnostic.
static void printFsContents() {
  FSInfo info;
  if (FatFS.info(info)) {
    Serial.print(F("FAT partition: "));
    Serial.print((unsigned)(info.totalBytes / 1024));
    Serial.print(F(" KB total, "));
    Serial.print((unsigned)(info.usedBytes / 1024));
    Serial.println(F(" KB used"));
  }

  Serial.println(F("files on drive:"));
  Dir dir = FatFS.openDir("/");
  bool any = false;
  while (dir.next()) {
    Serial.print(F("  "));
    Serial.print(dir.fileName());
    if (dir.isFile()) {
      Serial.print(F("  ("));
      Serial.print((unsigned)dir.fileSize());
      Serial.print(F(" bytes)"));
    } else {
      Serial.print(F("  <dir>"));
    }
    Serial.println();
    any = true;
  }
  if (!any) {
    Serial.println(F("  (none)"));
  }
}

void usbmscSetup() {
  // Mount once before exposing over USB so the host never sees an
  // unformatted drive. IMPORTANT: auto-format stays OFF so a transient mount
  // failure can never silently wipe the user's files (FatFS's default config
  // reformats on any failed mount!). Only an explicit, loudly-announced
  // format is allowed, for the genuinely-blank first boot.
  FatFS.setConfig(FatFSConfig(false));
  Serial.print(F("FatFS mount ... "));
  if (FatFS.begin()) {
    Serial.println(F("OK"));
  } else {
    Serial.println(F("FAILED — assuming blank partition, formatting now (this erases the drive!)"));
    FatFS.setConfig(FatFSConfig(true));  // one begin() with format permission
    bool ok = FatFS.begin();
    FatFS.setConfig(FatFSConfig(false));
    if (!ok) {
      Serial.println(F("format/mount FAILED — drive will look unformatted to the host"));
      return;
    }
    Serial.println(F("formatted + mounted OK"));
  }

  printFsContents();

  FatFS.end();  // host and firmware must never both own the filesystem

  FatFSUSB.driveReady(driveReadyCb);
  FatFSUSB.onUnplug(onUnplugCb);
  FatFSUSB.onPlug(onPlugCb);
  if (!FatFSUSB.begin()) {
    Serial.println(F("FatFSUSB.begin() FAILED"));
    return;
  }
  Serial.println(F("USB mass-storage drive exposed to host"));
}

bool usbmscEjectPending() {
  if (!ejectFlag) {
    return false;
  }
  ejectFlag = false;
  return true;
}

bool usbmscTakeOwnership() {
  fsOwned = true;  // host sees "not ready" from here on
  // auto-format is globally off (see usbmscSetup) — a mount failure here
  // must surface as an error, never as a silent wipe of the user's files
  if (!FatFS.begin()) {
    Serial.println(F("FatFS mount FAILED after eject (volume left untouched)"));
    fsOwned = false;
    return false;
  }
  printFsContents();
  return true;
}

void usbmscReleaseOwnership() {
  FatFS.end();
  fsOwned = false;
}

static bool hasJpegExt(String name) {
  name.toLowerCase();
  return name.endsWith(".jpg") || name.endsWith(".jpeg");
}

String usbmscFindLatestJpeg(size_t* sizeOut) {
  String best = "";
  time_t bestTime = 0;
  size_t bestSize = 0;

  Dir dir = FatFS.openDir("/");
  while (dir.next()) {
    if (!dir.isFile()) {
      continue;
    }
    String name = dir.fileName();
    if (name.startsWith(".") || name.startsWith("~")) {
      continue;  // hidden/temp droppings from the host OS
    }
    if (!hasJpegExt(name)) {
      continue;
    }
    time_t t = dir.fileTime();
    time_t ct = dir.fileCreationTime();
    if (ct > t) {
      t = ct;
    }
    if (best.length() == 0 || t > bestTime) {
      best = name;
      bestTime = t;
      bestSize = dir.fileSize();
    }
  }

  if (sizeOut) {
    *sizeOut = bestSize;
  }
  return best;
}

fs::FS& usbmscFilesystem() {
  return FatFS;
}
