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

import protocol as P
from dewhiten import dewhiten

PREAMBLE_BITS = "10101010" * 3
SYNC_BITS = format(P.SYNC_WORD[0], "08b") + format(P.SYNC_WORD[1], "08b")


def find_bursts(iq: np.ndarray, fs: float):
    env = np.convolve(np.abs(iq), np.ones(64) / 64, mode="same")
    floor = np.percentile(env, 20)
    active = env > max(floor * 4, floor + 0.05)
    edges = np.diff(active.astype(np.int8))
    starts = list(np.where(edges == 1)[0])
    ends = list(np.where(edges == -1)[0])
    if active[0]:
        starts.insert(0, 0)
    if active[-1]:
        ends.append(len(active) - 1)
    bursts = [(s, e) for s, e in zip(starts, ends) if (e - s) / fs > 0.005]
    return bursts, floor


def runlengths(bits: np.ndarray):
    edges = np.where(np.diff(bits.astype(np.int8)) != 0)[0]
    bounds = np.concatenate(([0], edges + 1, [len(bits)]))
    return np.diff(bounds), bits[bounds[:-1]]


def analyze_burst(iq: np.ndarray, fs: float, verbose: bool):
    """Returns a dict of measurements, plus decoded packets if any."""
    phase = np.angle(iq[1:] * np.conj(iq[:-1]))
    finst = phase * fs / (2 * np.pi)
    finst = np.convolve(finst, np.ones(9) / 9, mode="same")

    split = np.median(finst)
    f_lo = float(np.mean(finst[finst < split]))
    f_hi = float(np.mean(finst[finst >= split]))
    center = (f_lo + f_hi) / 2
    dev = (f_hi - f_lo) / 2

    # Symbol period from transition spacing: the preamble alternates every
    # bit, so one-bit gaps dominate the low end of the spacing histogram.
    # (A raw run-length mode is hopeless here — discriminator noise makes
    # 3-5-sample runs outnumber real symbols.)
    x = finst - center
    sign = x > 0
    trans = np.where(np.diff(sign.astype(np.int8)) != 0)[0]
    if len(trans) < 16:
        return None
    gaps = np.diff(trans).astype(float)
    gaps = gaps[(gaps >= 10) & (gaps <= 400)]
    if len(gaps) < 8:
        return None
    base = np.percentile(gaps, 20)
    one_bit = gaps[(gaps > 0.7 * base) & (gaps < 1.4 * base)]
    if len(one_bit) < 4:
        return None
    spb = float(np.median(one_bit))

    # Hysteresis slicer: only flip state on crossing the opposite threshold,
    # killing the discriminator-noise micro-runs that break naive slicing.
    hyst = dev * 0.4
    state = 1 if x[0] > 0 else 0
    sliced = np.empty(len(x), dtype=np.int8)
    for i, v in enumerate(x):
        if state == 0 and v > hyst:
            state = 1
        elif state == 1 and v < -hyst:
            state = 0
        sliced[i] = state
    runs, values = runlengths(sliced)

    # Refine the symbol period over the whole burst (each run is an integer
    # number of bits): two rounds of  spb = sum(runs)/sum(round(runs/spb)).
    for _ in range(2):
        counts = np.maximum(np.round(runs / spb), 1)
        spb = float(np.sum(runs) / np.sum(counts))
    bitrate = fs / spb

    # Run-length decoding has no cumulative clock drift: each run is
    # quantized independently, and whitening caps runs at ~9 bits.
    bits = []
    for run, val in zip(runs, values):
        bits.extend([int(val)] * int(max(np.round(run / spb), 1)))
    bitstr = "".join(map(str, bits))

    result = {
        "center_hz": center, "dev_hz": dev, "bitrate": bitrate,
        "n_bits": len(bitstr), "packets": [], "sync": None,
    }

    for polarity, bs in (("normal", bitstr),
                         ("inverted", bitstr.translate(str.maketrans("01", "10")))):
        if PREAMBLE_BITS not in bs:
            continue
        idx = bs.find(SYNC_BITS, bs.find(PREAMBLE_BITS))
        if idx < 0:
            result["sync"] = result["sync"] or f"preamble only ({polarity})"
            continue
        result["sync"] = f"found ({polarity} polarity)"
        payload_bits = bs[idx + len(SYNC_BITS):]
        nbytes = len(payload_bits) // 8
        raw = bytes(int(payload_bits[i * 8:(i + 1) * 8], 2) for i in range(nbytes))
        data = dewhiten(raw)
        if len(data) >= 1:
            plen = data[0]
            if 1 <= plen <= 60 and len(data) >= 1 + plen + 2:
                body = data[1:1 + plen]
                crc_rx = (data[1 + plen] << 8) | data[2 + plen]
                crc_ok = P.crc16_cc1101(data[:1 + plen]) == crc_rx
                result["packets"].append({
                    "len": plen, "crc_ok": crc_ok,
                    "magic_ok": body[0] == P.PKT_MAGIC if plen else False,
                    "body": body.hex(),
                })
        break
    return result


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
          f"(expect ~117 ms data, ~37 ms header)")

    crc_pass = crc_fail = 0
    for i, (s, e) in enumerate(bursts[: args.max_bursts]):
        r = analyze_burst(iq[max(s - 50, 0):e + 50], fs, args.verbose)
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
