"""Pure-numpy CC1101 2-FSK burst demodulator.

Shared by analyze_capture.py (offline analysis) and live_rx.py (the live
receiver). This is the fallback path CLAUDE.md anticipated: rtl_433's flex
decoder never fired on this signal, while this chain decodes it CRC-clean
(proven against real captures on 2026-07-02).

Chain per burst: FM discriminator -> two-tone estimate -> hysteresis
slicer -> whole-burst symbol-clock refinement -> run-length bit decode ->
preamble/sync search (both polarities) -> PN9 de-whiten -> length byte ->
CC1101 CRC-16 verify.
"""

import numpy as np

import protocol as P
from dewhiten import dewhiten

PREAMBLE_BITS = "10101010" * 3
SYNC_BITS = format(P.SYNC_WORD[0], "08b") + format(P.SYNC_WORD[1], "08b")


def find_bursts(iq: np.ndarray, fs: float, floor=None):
    """Envelope-threshold burst segmentation.

    Returns (bursts, chunk_floor): bursts as (start, end) sample indexes —
    a burst still in progress at the buffer end is reported with
    end == len(iq)-1 — and this chunk's noise-floor estimate.

    The floor is the 5th percentile of the envelope: the transmitter's
    packet train is ~92% duty (117 ms bursts, 10 ms gaps), so a higher
    percentile (or a mean) measures the SIGNAL and the threshold creeps up
    until reception goes deaf — that exact failure was observed live.
    The 10 ms gaps are ~8% of airtime, so the 5th percentile stays in the
    gaps and reads true noise even mid-transmission.
    """
    env = np.convolve(np.abs(iq), np.ones(64) / 64, mode="same")
    chunk_floor = float(np.percentile(env, 5))
    if floor is None:
        floor = chunk_floor
    # Purely relative threshold (14 dB over the floor). An absolute term
    # would silently reject weaker-but-clean signals.
    active = env > floor * 5
    edges = np.diff(active.astype(np.int8))
    starts = list(np.where(edges == 1)[0])
    ends = list(np.where(edges == -1)[0])
    if active[0]:
        starts.insert(0, 0)
    if active[-1]:
        ends.append(len(active) - 1)
    bursts = [(s, e) for s, e in zip(starts, ends) if (e - s) / fs > 0.005]
    return bursts, chunk_floor


def _runlengths(bits: np.ndarray):
    edges = np.where(np.diff(bits.astype(np.int8)) != 0)[0]
    bounds = np.concatenate(([0], edges + 1, [len(bits)]))
    return np.diff(bounds), bits[bounds[:-1]]


def demod_burst(iq: np.ndarray, fs: float):
    """Demodulate one burst. Returns a dict with the measured PHY numbers,
    the sliced bitstream, and any CRC-checkable packets found — or None if
    the burst is too short/noisy to slice."""
    phase = np.angle(iq[1:] * np.conj(iq[:-1]))
    finst = phase * fs / (2 * np.pi)
    finst = np.convolve(finst, np.ones(9) / 9, mode="same")

    split = np.median(finst)
    f_lo = float(np.mean(finst[finst < split]))
    f_hi = float(np.mean(finst[finst >= split]))
    center = (f_lo + f_hi) / 2
    dev = (f_hi - f_lo) / 2

    # Symbol period seed from transition spacing: the preamble alternates
    # every bit, so one-bit gaps dominate the low end of the histogram.
    x = finst - center
    trans = np.where(np.diff((x > 0).astype(np.int8)) != 0)[0]
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

    # Hysteresis slicer: flip only on crossing the opposite threshold,
    # killing discriminator-noise micro-runs.
    hyst = dev * 0.4
    state = 1 if x[0] > 0 else 0
    sliced = np.empty(len(x), dtype=np.int8)
    for i, v in enumerate(x):
        if state == 0 and v > hyst:
            state = 1
        elif state == 1 and v < -hyst:
            state = 0
        sliced[i] = state
    runs, values = _runlengths(sliced)

    # Refine the symbol period over the whole burst (each run is an integer
    # number of bits) — kills the clock drift that corrupts long packets.
    for _ in range(2):
        counts = np.maximum(np.round(runs / spb), 1)
        spb = float(np.sum(runs) / np.sum(counts))

    # Run-length decode: no cumulative drift, each run quantized on its own;
    # PN9 whitening caps legitimate runs at ~9 bits.
    bits = []
    for run, val in zip(runs, values):
        bits.extend([int(val)] * int(max(np.round(run / spb), 1)))
    bitstr = "".join(map(str, bits))

    sync_note = {}
    packets = extract_packets(bitstr, sync_note)
    return {
        "center_hz": center,
        "dev_hz": dev,
        "bitrate": fs / spb,
        "n_bits": len(bitstr),
        "sync": sync_note.get("sync"),
        "packets": packets,
    }


def extract_packets(bitstr: str, sync_note: dict):
    """Search the bitstream for preamble+sync (both polarities), de-whiten,
    and CRC-check. Returns a list of packets; each has 'data' = de-whitened
    bytes from the length byte through the CRC, ready for a Reassembler."""
    packets = []
    for polarity, bs in (("normal", bitstr),
                         ("inverted", bitstr.translate(str.maketrans("01", "10")))):
        if PREAMBLE_BITS not in bs:
            continue
        idx = bs.find(SYNC_BITS, bs.find(PREAMBLE_BITS))
        if idx < 0:
            sync_note.setdefault("sync", f"preamble only ({polarity})")
            continue
        sync_note["sync"] = f"found ({polarity} polarity)"
        payload_bits = bs[idx + len(SYNC_BITS):]
        nbytes = len(payload_bits) // 8
        raw = bytes(int(payload_bits[i * 8:(i + 1) * 8], 2) for i in range(nbytes))
        data = dewhiten(raw)
        if len(data) >= 1:
            plen = data[0]
            if 1 <= plen <= 60 and len(data) >= 1 + plen + 2:
                data = data[:1 + plen + 2]
                body = data[1:1 + plen]
                crc_rx = (data[1 + plen] << 8) | data[2 + plen]
                packets.append({
                    "data": data,
                    "len": plen,
                    "crc_ok": P.crc16_cc1101(data[:1 + plen]) == crc_rx,
                    "magic_ok": bool(plen) and body[0] == P.PKT_MAGIC,
                    "body": body.hex(),
                })
        break
    return packets
