"""Minimal ctypes wrapper for librtlsdr, shared by spectrum.py and
capture_iq.py.

Talks to the librtlsdr.dll bundled with the rtl_433 install (or whatever
the system provides). Exists because pyrtlsdr's current release fails to
load vanilla librtlsdr builds (it requires the fork-only
rtlsdr_set_dithering symbol at import time).
"""

import ctypes
import ctypes.util
import os
import sys

import numpy as np


def _load_librtlsdr() -> ctypes.CDLL:
    candidates = []
    if os.name == "nt":
        rtl433_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                  "Programs", "rtl_433")
        if os.path.isdir(rtl433_dir):
            os.add_dll_directory(rtl433_dir)
            candidates.append(os.path.join(rtl433_dir, "librtlsdr.dll"))
        candidates.append("librtlsdr.dll")
    found = ctypes.util.find_library("rtlsdr")
    if found:
        candidates.append(found)
    candidates.append("librtlsdr.so.0")
    for cand in candidates:
        try:
            return ctypes.CDLL(cand)
        except OSError:
            continue
    sys.exit("librtlsdr not found — expected e.g. "
             r"%LOCALAPPDATA%\Programs\rtl_433\librtlsdr.dll")


class MiniRtlSdr:
    """The handful of librtlsdr calls these tools need."""

    def __init__(self, freq_hz: float, rate_hz: float, gain):
        self.lib = _load_librtlsdr()
        if self.lib.rtlsdr_get_device_count() < 1:
            sys.exit("no RTL-SDR device found (unplugged, or owned by "
                     "another program? close rtl_433 / SDR apps)")
        self.dev = ctypes.c_void_p()
        if self.lib.rtlsdr_open(ctypes.byref(self.dev), 0) != 0:
            sys.exit("rtlsdr_open failed — device probably in use by "
                     "another program (close rtl_433 / SDR apps)")
        self.lib.rtlsdr_set_sample_rate(self.dev, int(rate_hz))
        self.lib.rtlsdr_set_center_freq(self.dev, int(freq_hz))
        if gain == "auto":
            self.lib.rtlsdr_set_tuner_gain_mode(self.dev, 0)
        else:
            self.lib.rtlsdr_set_tuner_gain_mode(self.dev, 1)
            self.lib.rtlsdr_set_tuner_gain(self.dev, self._snap_gain(gain))
        self.lib.rtlsdr_reset_buffer(self.dev)

    def _snap_gain(self, gain_db: float) -> int:
        """librtlsdr wants one of the tuner's supported gains, tenths of dB."""
        n = self.lib.rtlsdr_get_tuner_gains(self.dev, None)
        if n <= 0:
            return int(float(gain_db) * 10)
        arr = (ctypes.c_int * n)()
        self.lib.rtlsdr_get_tuner_gains(self.dev, arr)
        want = float(gain_db) * 10
        snapped = min(arr, key=lambda g: abs(g - want))
        print(f"gain {snapped / 10:.1f} dB "
              f"(nearest supported to requested {gain_db})")
        return snapped

    def read_raw(self, n_bytes: int) -> np.ndarray:
        """Raw interleaved uint8 I/Q bytes (cu8), n_bytes multiple of 512."""
        assert n_bytes % 512 == 0, "librtlsdr reads must be multiples of 512"
        buf = (ctypes.c_ubyte * n_bytes)()
        n_read = ctypes.c_int(0)
        if self.lib.rtlsdr_read_sync(self.dev, buf, n_bytes,
                                     ctypes.byref(n_read)) != 0:
            sys.exit("rtlsdr_read_sync failed")
        return np.frombuffer(buf, dtype=np.uint8, count=n_read.value).copy()

    def read_iq(self, n_samples: int) -> np.ndarray:
        raw = self.read_raw(n_samples * 2).astype(np.float32)
        iq = (raw - 127.5) / 127.5
        return iq[0::2] + 1j * iq[1::2]

    def close(self) -> None:
        self.lib.rtlsdr_close(self.dev)
