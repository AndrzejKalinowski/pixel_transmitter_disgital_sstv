#!/usr/bin/env python3
"""Offline FSK analysis of a capture_iq.py recording.

Measures what the transmitter ACTUALLY puts on the air — burst timing,
carrier offset, FSK deviation, bit rate, bit polarity — then tries a full
packet decode (preamble/sync search, PN9 de-whiten, CC1101 CRC check).
Reports every stage, so whatever assumption the rtl_433 flex spec gets
wrong shows up here by name.

Usage:
    python analyze_capture.py [capture.cu8] [--rate 250e3] [--max-bursts 12]
"""

import argparse
import os
import sys

import numpy as np

from fskdemod import demod_burst, find_bursts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", nargs="?",
                    default=os.path.join(os.path.dirname(__file__) or ".",
                                         "capture.cu8"))
    ap.add_argument("--rate", type=float, default=250e3)
    ap.add_argument("--max-bursts", type=int, default=12)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    raw = np.fromfile(args.capture, dtype=np.uint8).astype(np.float32)
    iq = ((raw[0::2] - 127.5) + 1j * (raw[1::2] - 127.5)) / 127.5
    fs = args.rate
    print(f"{args.capture}: {len(iq)} samples = {len(iq) / fs:.1f} s at "
          f"{fs / 1e3:.0f} kS/s")

    bursts, floor = find_bursts(iq, fs)
    print(f"noise floor {20 * np.log10(floor + 1e-9):.1f} dBFS, "
          f"{len(bursts)} bursts found")
    if not bursts:
        sys.exit("no bursts — was the transmitter running during capture?")

    durs = [(e - s) / fs * 1e3 for s, e in bursts]
    print(f"burst durations: min {min(durs):.1f} / median "
          f"{sorted(durs)[len(durs) // 2]:.1f} / max {max(durs):.1f} ms "
          f"(expect ~58 ms data / ~18 ms header at 9.6 kbps; "
          f"double that for old 4.8 kbps captures)")

    crc_pass = crc_fail = 0
    for i, (s, e) in enumerate(bursts[: args.max_bursts]):
        r = demod_burst(iq[max(s - 50, 0):e + 50], fs)
        if r is None:
            print(f"burst {i}: too short/noisy to slice")
            continue
        line = (f"burst {i}: offset {r['center_hz']:+6.0f} Hz, "
                f"dev +/-{r['dev_hz']:.0f} Hz, {r['bitrate']:.0f} bps, "
                f"{r['n_bits']} bits, sync: {r['sync'] or 'NOT FOUND'}")
        for p in r["packets"]:
            crc_pass += p["crc_ok"]
            crc_fail += not p["crc_ok"]
            line += (f"\n         packet len={p['len']} "
                     f"crc={'OK' if p['crc_ok'] else 'FAIL'} "
                     f"magic={'OK' if p['magic_ok'] else 'BAD'}")
            if args.verbose:
                line += f" body={p['body']}"
        print(line)

    print("\n--- verdict ---")
    if crc_pass:
        print(f"{crc_pass} packets decoded with valid CRC: the on-air signal "
              f"matches the protocol. If rtl_433 still prints nothing, the "
              f"problem is its demod front-end settings (gain/frequency), "
              f"not the flex spec.")
    else:
        print("no CRC-valid packets — compare the measured offset/deviation/"
              "bitrate/polarity above against the flex spec assumptions "
              "(208 us/bit, dev ~5 kHz, preamble aad391).")


if __name__ == "__main__":
    main()
