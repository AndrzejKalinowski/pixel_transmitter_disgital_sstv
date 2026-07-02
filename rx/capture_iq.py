#!/usr/bin/env python3
"""Record raw IQ from the RTL-SDR to a .cu8 file for offline analysis.

Debugging tool: when rtl_433 decodes nothing, capture the actual RF and
analyze the FSK offline (bit rate, deviation, preamble, sync) instead of
guessing at flex-decoder parameters.

Usage:
    python capture_iq.py --seconds 15
    ...then EJECT on the Pico so the transmission runs inside the window.

Prints a level read-out twice a second — transmit bursts are clearly
marked, so you can see whether anything was actually captured before
handing the file over for analysis. Output defaults to rx/capture.cu8
(interleaved uint8 I/Q, the same format rtl_sdr/rtl_433 use).
"""

import argparse
import os
import time

import numpy as np

from rtlsdr_mini import MiniRtlSdr


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--freq", type=float, default=433.98e6,
                    help="center frequency, Hz (bench-measured carrier)")
    ap.add_argument("--rate", type=float, default=250e3, help="sample rate, Hz")
    ap.add_argument("--gain", default="20", help="tuner gain dB or 'auto'")
    ap.add_argument("--seconds", type=float, default=15.0, help="capture length")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__) or ".",
                                                  "capture.cu8"))
    args = ap.parse_args()

    sdr = MiniRtlSdr(args.freq, args.rate, args.gain)
    total_bytes = int(args.rate * 2 * args.seconds)
    chunk = 256 * 1024  # ~0.5 s at 250 kS/s, multiple of 512

    print(f"capturing {args.seconds:.0f} s at {args.freq / 1e6:.3f} MHz, "
          f"{args.rate / 1e3:.0f} kS/s -> {args.out}")
    print("trigger the transmission NOW (eject the Pico drive)")

    noise_floor = None
    written = 0
    t0 = time.time()
    with open(args.out, "wb") as fh:
        while written < total_bytes:
            raw = sdr.read_raw(chunk)
            raw.tofile(fh)
            written += len(raw)

            iq = (raw.astype(np.float32) - 127.5) / 127.5
            rms = float(np.sqrt(np.mean(iq[0::2] ** 2 + iq[1::2] ** 2)))
            db = 20 * np.log10(rms + 1e-9)
            noise_floor = db if noise_floor is None else min(noise_floor, db)
            burst = "  <-- signal!" if db > noise_floor + 6 else ""
            print(f"  t={time.time() - t0:5.1f} s  level {db:6.1f} dBFS{burst}")
    sdr.close()
    print(f"done: {written} bytes ({written // 2} samples) in {args.out}")


if __name__ == "__main__":
    main()
