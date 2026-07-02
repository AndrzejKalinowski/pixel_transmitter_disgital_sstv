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

SYNC_BITS = format(P.SYNC_WORD[0], "08b") + format(P.SYNC_WORD[1], "08b")
_SYNC_ARR = np.array([int(b) for b in SYNC_BITS], dtype=np.uint8)


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
    # Trim to the actual signal extent first. The segmentation threshold is
    # deliberately generous, so segments carry noise margins — and the
    # hysteresis slicer chattering in those margins drags the symbol-clock
    # refinement off frequency (short header packets died of exactly this).
    env = np.convolve(np.abs(iq), np.ones(32) / 32, mode="same")
    ref = np.median(env[len(env) // 4 : 3 * len(env) // 4])
    on = np.where(env > 0.5 * ref)[0]
    if len(on) < 200:
        return None
    iq = iq[on[0] : on[-1] + 1]

    phase = np.angle(iq[1:] * np.conj(iq[:-1]))
    finst = phase * fs / (2 * np.pi)
    finst = np.convolve(finst, np.ones(9) / 9, mode="same")

    # Estimate the two FSK tones from the central 60% of the burst: the
    # segmentation includes noise margins at both ends, and letting those
    # into the tone means biases the slicing threshold on marginal bursts.
    core = finst[len(finst) // 5 : len(finst) - len(finst) // 5]
    if len(core) < 64:
        return None
    split = np.median(core)
    f_lo = float(np.mean(core[core < split]))
    f_hi = float(np.mean(core[core >= split]))
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
    bits = np.concatenate([
        np.full(int(max(np.round(run / spb), 1)), val, dtype=np.uint8)
        for run, val in zip(runs, values)
    ])

    sync_note = {}
    packets = extract_packets(bits, sync_note)
    return {
        "center_hz": center,
        "dev_hz": dev,
        "bitrate": fs / spb,
        "n_bits": len(bits),
        "sync": sync_note.get("sync"),
        "packets": packets,
    }


def _try_at(bits: np.ndarray, start: int):
    """Attempt a packet parse with the payload starting at bit `start`.
    Returns a packet dict (crc_ok True/False) or None if too short/absurd."""
    avail = (len(bits) - start) // 8
    if avail < 4:  # length byte + minimal body + CRC can't fit
        return None
    raw = np.packbits(bits[start:start + avail * 8]).tobytes()
    data = dewhiten(raw)
    plen = data[0]
    if not (1 <= plen <= 60) or len(data) < 1 + plen + 2:
        return None
    data = data[:1 + plen + 2]
    body = data[1:1 + plen]
    crc_rx = (data[1 + plen] << 8) | data[2 + plen]
    return {
        "data": data,
        "len": plen,
        "crc_ok": P.crc16_cc1101(data[:1 + plen]) == crc_rx,
        "magic_ok": bool(plen) and body[0] == P.PKT_MAGIC,
        "body": body.hex(),
    }


def extract_packets(bits: np.ndarray, sync_note: dict):
    """Find the packet in a sliced bitstream and CRC-check it.

    Instead of requiring a verbatim preamble+sync match (where one sliced
    bit error kills an otherwise-perfect packet), correlate the 16-bit sync
    word across the whole stream tolerating <=1 bit error — the same
    leniency a real CC1101 uses in 15/16-match sync mode. Every candidate
    alignment is arbitrated by the CRC (a false anchor passing CRC-16 is a
    ~2e-5 event), exact matches tried first. Both FSK polarities are tried.

    Returns at most one packet; if no candidate passes CRC, the best
    candidate is returned with crc_ok=False so callers can count the loss.
    """
    best_fail = None
    for polarity in ("normal", "inverted"):
        bb = bits if polarity == "normal" else (1 - bits).astype(np.uint8)
        if len(bb) < len(_SYNC_ARR) + 32:
            continue
        windows = np.lib.stride_tricks.sliding_window_view(bb, len(_SYNC_ARR))
        mismatches = (windows != _SYNC_ARR).sum(axis=1)
        cand = np.where(mismatches <= 1)[0]
        if len(cand) == 0:
            continue
        cand = cand[np.argsort(mismatches[cand], kind="stable")][:8]
        for idx in cand:
            pkt = _try_at(bb, int(idx) + len(_SYNC_ARR))
            if pkt is None:
                continue
            if pkt["crc_ok"]:
                sync_note["sync"] = (f"found ({polarity}, "
                                     f"{int(mismatches[idx])} sync-bit err)")
                return [pkt]
            if best_fail is None:
                best_fail = pkt
                sync_note.setdefault("sync", f"candidate only ({polarity})")
    return [best_fail] if best_fail else []
