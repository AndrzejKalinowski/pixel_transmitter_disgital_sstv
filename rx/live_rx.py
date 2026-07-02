#!/usr/bin/env python3
"""Live pure-Python receiver: RTL-SDR -> FSK demod -> tile canvas -> out.png.

This is the fallback path CLAUDE.md anticipated, taken 2026-07-02 after the
rtl_433 flex-decoder path was genuinely exhausted (its FSK detector never
fired on this signal even with the gain and DC-spike issues fixed, while
this demodulator decodes the same RF CRC-clean — see rx/analyze_capture.py
and the CLAUDE.md gotcha checklist for the measurements).

Usage:
    python live_rx.py                 # listen until Ctrl-C
    python live_rx.py --show          # + live OpenCV window
    python live_rx.py --file capture.cu8   # replay a recording instead

The Reassembler (reassemble.py) does the painting: gray canvas, completed
tiles blitted as they arrive, accumulation across repeat transmissions,
out.png rewritten on every completed tile.
"""

import argparse
import os
import sys
import time

import numpy as np

from fskdemod import demod_burst, find_bursts
from reassemble import Reassembler


def iq_from_dongle(freq, rate, gain, chunk_samples):
    from rtlsdr_mini import MiniRtlSdr
    sdr = MiniRtlSdr(freq, rate, gain)
    try:
        while True:
            yield sdr.read_iq(chunk_samples)
    finally:
        sdr.close()


def iq_from_file(path, chunk_samples):
    raw = np.fromfile(path, dtype=np.uint8).astype(np.float32)
    iq = ((raw[0::2] - 127.5) + 1j * (raw[1::2] - 127.5)) / 127.5
    for i in range(0, len(iq), chunk_samples):
        yield iq[i:i + chunk_samples]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--freq", type=float, default=433.96e6,
                    help="tuner center, Hz. Off-center on purpose: keeps the "
                         "FSK tones (carrier ~433.985M on this bench) away "
                         "from the dongle's DC spike")
    ap.add_argument("--rate", type=float, default=250e3)
    ap.add_argument("--gain", default="20",
                    help="tuner gain dB or 'auto' (auto clips on this bench)")
    ap.add_argument("--file", help="replay a capture_iq.py .cu8 recording")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__) or ".",
                                                  "out.png"))
    ap.add_argument("--show", action="store_true", help="live OpenCV window")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print a line per burst")
    args = ap.parse_args()

    fs = args.rate
    chunk = 131072  # ~0.52 s at 250 kS/s
    margin = int(0.03 * fs)  # a burst must end this clear of the buffer tail

    rx = Reassembler(args.out, args.show, args.verbose)
    rx.save()

    if args.file:
        source = iq_from_file(args.file, chunk)
        print(f"replaying {args.file}")
    else:
        source = iq_from_dongle(args.freq, fs, args.gain, chunk)
        print(f"listening at {args.freq / 1e6:.3f} MHz, gain {args.gain} — "
              f"eject the Pico drive to transmit; Ctrl-C to stop")

    buf = np.empty(0, dtype=np.complex64)
    floor = None
    bursts_seen = 0
    last_status = time.time()

    try:
        for iq in source:
            buf = np.concatenate([buf, iq.astype(np.complex64)])

            bursts, chunk_floor = find_bursts(buf, fs, floor)
            # Long-term noise floor: follow drops immediately, rise slowly,
            # so a dense packet train can't drag the threshold up onto the
            # signal itself.
            floor = chunk_floor if floor is None else min(floor * 1.02, chunk_floor)

            consumed = len(buf) - margin
            for s, e in bursts:
                if e >= len(buf) - margin:
                    consumed = min(consumed, max(s - 50, 0))  # still in progress
                    continue
                bursts_seen += 1
                r = demod_burst(buf[max(s - 50, 0):e + 50], fs)
                if r is None:
                    continue
                if args.verbose:
                    print(f"burst {bursts_seen}: {(e - s) / fs * 1e3:5.1f} ms, "
                          f"offset {r['center_hz']:+6.0f} Hz, "
                          f"{r['bitrate']:.0f} bps, sync {r['sync'] or 'none'}")
                for pkt in r["packets"]:
                    if pkt["crc_ok"]:
                        rx.process_packet(pkt["data"])
                    else:
                        rx.stats["crc_fail"] += 1
            buf = buf[max(consumed, 0):]
            if len(buf) > int(5 * fs):  # safety valve, should never trigger
                buf = buf[-int(fs):]

            if time.time() - last_status > 5:
                last_status = time.time()
                print(f"[status] bursts {bursts_seen} | packets ok "
                      f"{rx.stats['crc_ok'] + rx.stats['crc_ok_swapped']} | "
                      f"crc fail {rx.stats['crc_fail']} | tiles "
                      f"{len(rx.done)}/{rx.total_tiles}")
    except KeyboardInterrupt:
        pass
    finally:
        rx.save()
        rx.summary()


if __name__ == "__main__":
    main()
