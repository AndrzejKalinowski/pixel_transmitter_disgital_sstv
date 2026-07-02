#!/usr/bin/env python3
"""Live pure-Python receiver: RTL-SDR -> FSK demod -> tile canvas -> out.png.

This is the fallback path CLAUDE.md anticipated, taken 2026-07-02 after the
rtl_433 flex-decoder path was genuinely exhausted (its FSK detector never
fired on this signal even with the gain and DC-spike issues fixed, while
this demodulator decodes the same RF CRC-clean — see rx/analyze_capture.py
and the CLAUDE.md gotcha checklist for the measurements).

Usage:
    python live_rx.py                   # listen until Ctrl-C
    python live_rx.py --waterfall       # + live spectrogram window
    python live_rx.py --show            # + live image window (OpenCV)
    python live_rx.py --record rf.cu8   # tee raw IQ to a file (post-mortem)
    python live_rx.py --file rf.cu8     # replay a recording instead

The status line is the health readout:
    floor  = tracked noise floor (should sit near the idle level, ~-45 dB
             on this bench, NOT climb during a transmission)
    envmax = strongest envelope since the last status line (bursts push
             this 15+ dB above the floor; if it is high while bursts stay
             at 0, burst detection is broken — if it is low, there is no RF)
    buf    = working buffer length (should hover near 0.5 s; growth means
             the demod is falling behind real time)
"""

import argparse
import os
import time

import numpy as np

from fskdemod import demod_burst, find_bursts
from reassemble import Reassembler


def raw_from_dongle(freq, rate, gain, chunk_bytes):
    from rtlsdr_mini import MiniRtlSdr
    sdr = MiniRtlSdr(freq, rate, gain)
    try:
        while True:
            yield sdr.read_raw(chunk_bytes)
    finally:
        sdr.close()


def raw_from_file(path, chunk_bytes):
    data = np.fromfile(path, dtype=np.uint8)
    for i in range(0, len(data), chunk_bytes):
        yield data[i:i + chunk_bytes]


def to_iq(raw: np.ndarray) -> np.ndarray:
    f = raw.astype(np.float32)
    return (((f[0::2] - 127.5) + 1j * (f[1::2] - 127.5)) / 127.5).astype(np.complex64)


class Waterfall:
    """Small live spectrogram, one row per chunk (~0.5 s)."""

    def __init__(self, freq_hz: float, rate_hz: float, n_fft=512, rows=120):
        import matplotlib.pyplot as plt
        self.plt = plt
        self.n_fft = n_fft
        self.window = np.hanning(n_fft)
        self.wf = np.full((rows, n_fft), -80.0)
        freqs_khz = np.fft.fftshift(np.fft.fftfreq(n_fft, 1.0 / rate_hz)) / 1e3

        plt.ion()
        self.fig, ax = plt.subplots(figsize=(8, 4))
        self.fig.canvas.manager.set_window_title("live_rx waterfall")
        self.im = ax.imshow(self.wf, aspect="auto", origin="lower",
                            extent=[freqs_khz[0], freqs_khz[-1], 0, rows],
                            vmin=-70, vmax=-10, cmap="viridis")
        ax.set_xlabel(f"offset from {freq_hz / 1e6:.3f} MHz [kHz]")
        ax.set_ylabel(f"time [chunks, newest top]")
        self.fig.tight_layout()

    def update(self, iq: np.ndarray) -> None:
        n = (len(iq) // self.n_fft) * self.n_fft
        if not n:
            return
        frames = iq[:n].reshape(-1, self.n_fft) * self.window
        spec = np.fft.fftshift(np.abs(np.fft.fft(frames, axis=1)) ** 2, axes=1)
        db = 10 * np.log10(spec.mean(axis=0) / (self.n_fft * self.n_fft) + 1e-12)
        self.wf = np.roll(self.wf, 1, axis=0)
        self.wf[0] = db
        self.im.set_data(self.wf[::-1])
        self.fig.canvas.draw_idle()
        self.plt.pause(0.001)


def db(x: float) -> float:
    return 20 * np.log10(x + 1e-9)


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
    ap.add_argument("--record", help="tee raw IQ to this .cu8 file while receiving")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__) or ".",
                                                  "out.png"))
    ap.add_argument("--show", action="store_true", help="live image window (OpenCV)")
    ap.add_argument("--waterfall", action="store_true",
                    help="live spectrogram window (matplotlib)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print a line per burst")
    args = ap.parse_args()

    fs = args.rate
    chunk_bytes = 262144  # ~0.52 s at 250 kS/s, multiple of 512
    margin = int(0.03 * fs)

    rx = Reassembler(args.out, args.show, args.verbose)
    rx.save(force=True)

    if args.file:
        source = raw_from_file(args.file, chunk_bytes)
        print(f"replaying {args.file}")
    else:
        source = raw_from_dongle(args.freq, fs, args.gain, chunk_bytes)
        print(f"listening at {args.freq / 1e6:.3f} MHz, gain {args.gain} — "
              f"eject the Pico drive to transmit; Ctrl-C to stop")

    waterfall = Waterfall(args.freq, fs) if args.waterfall else None
    record_fh = open(args.record, "wb") if args.record else None

    buf = np.empty(0, dtype=np.complex64)
    floor = None
    bursts_seen = 0
    envmax_window = 0.0
    last_status = time.time()

    try:
        for raw in source:
            if record_fh:
                raw.tofile(record_fh)
            iq = to_iq(raw)
            if waterfall:
                waterfall.update(iq)
            envmax_window = max(envmax_window, float(np.abs(iq).max()))
            buf = np.concatenate([buf, iq])

            bursts, chunk_floor = find_bursts(buf, fs, floor)
            # Track noise, never the signal: drop to a lower measurement
            # immediately, rise at most 5% per chunk. chunk_floor is a 5th
            # percentile, so it reads gap noise even mid-transmission.
            floor = chunk_floor if floor is None else min(floor * 1.05, chunk_floor)

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
                print("WARNING: demod fell >5 s behind, dropping buffer")
                buf = buf[-int(fs):]

            if time.time() - last_status > 5:
                last_status = time.time()
                print(f"[status] bursts {bursts_seen} | packets ok "
                      f"{rx.stats['crc_ok'] + rx.stats['crc_ok_swapped']} | "
                      f"crc fail {rx.stats['crc_fail']} | tiles "
                      f"{len(rx.done)}/{rx.total_tiles} | floor {db(floor):5.1f} dB"
                      f" | envmax {db(envmax_window):5.1f} dB | "
                      f"buf {len(buf) / fs:.1f} s")
                envmax_window = 0.0
    except KeyboardInterrupt:
        pass
    finally:
        if record_fh:
            record_fh.close()
        rx.save(force=True)
        rx.summary()


if __name__ == "__main__":
    main()
