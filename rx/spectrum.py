#!/usr/bin/env python3
"""Live spectrum + waterfall around the transmitter frequency.

Diagnostic tool: shows whether the CC1101 bursts actually reach the RTL-SDR,
how strong they are, and how far off 434.000 MHz they sit (both dongle and
CC1101 crystals have offsets). Uses the same dongle as rtl_433, so CLOSE
rtl_433 / SDR apps first — the dongle has exactly one owner at a time.

Talks to librtlsdr.dll directly via ctypes (reusing the DLL that ships with
rtl_433) — no pyrtlsdr needed; its current release refuses to load vanilla
librtlsdr builds.

Usage:
    python spectrum.py                  # 434.0 MHz center, 250 kHz span
    python spectrum.py --gain 1         # fixed low gain (bench overload)
    python spectrum.py --probe          # no GUI: open dongle, print levels

What to look for while the Pico transmits (one burst every ~63 ms):
  * Healthy: a narrow (~30 kHz wide) blip flickering near the center line.
    The title's peak read-out tells you its exact offset — if it is more
    than ~20 kHz off, retune rtl_433 with e.g.  $env:FREQ="433.98M".
  * Nothing at all: RF-side problem — is the firmware actually printing
    "tile x/64" right now? antenna attached? try --gain 20 or closer range.
  * Full-width splatter on every burst: front end overloaded — lower gain,
    pull the SDR antenna, add distance.
"""

import argparse
import time

import numpy as np

from rtlsdr_mini import MiniRtlSdr


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--freq", type=float, default=434.0e6, help="center frequency, Hz")
    ap.add_argument("--rate", type=float, default=250e3, help="sample rate / span, Hz")
    ap.add_argument("--gain", default="auto",
                    help="tuner gain in dB (e.g. 1, 20, 40) or 'auto'")
    ap.add_argument("--fft", type=int, default=1024, help="FFT size")
    ap.add_argument("--rows", type=int, default=200, help="waterfall history rows")
    ap.add_argument("--probe", action="store_true",
                    help="no GUI: read 2 s, print signal levels, exit")
    args = ap.parse_args()

    sdr = MiniRtlSdr(args.freq, args.rate, args.gain)

    if args.probe:
        print(f"probing {args.freq / 1e6:.3f} MHz for 2 s...")
        peak_db = -120.0
        for _ in range(30):
            iq = sdr.read_iq(16384)
            rms = float(np.sqrt(np.mean(np.abs(iq) ** 2)))
            db = 20 * np.log10(rms + 1e-9)
            peak_db = max(peak_db, db)
            time.sleep(0.01)
        sdr.close()
        print(f"peak wideband level over 2 s: {peak_db:.1f} dBFS")
        print("(a transmitting CC1101 nearby should push this well above "
              "the idle noise floor; run once with TX off to compare)")
        return

    import matplotlib.pyplot as plt

    n_fft = args.fft
    freqs_khz = np.fft.fftshift(np.fft.fftfreq(n_fft, 1.0 / args.rate)) / 1e3
    waterfall = np.full((args.rows, n_fft), -80.0)
    window = np.hanning(n_fft)

    plt.ion()
    fig, (ax_spec, ax_wf) = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True,
        gridspec_kw={"height_ratios": [1, 2]})
    fig.canvas.manager.set_window_title("pixel_transmitter spectrum")

    (line,) = ax_spec.plot(freqs_khz, waterfall[0], lw=0.8)
    ax_spec.set_ylim(-80, 0)
    ax_spec.set_ylabel("dBFS")
    ax_spec.axvline(0, color="red", lw=0.5, alpha=0.5)
    ax_spec.grid(True, alpha=0.3)

    im = ax_wf.imshow(waterfall, aspect="auto", origin="lower",
                      extent=[freqs_khz[0], freqs_khz[-1], 0, args.rows],
                      vmin=-70, vmax=-10, cmap="viridis")
    ax_wf.set_xlabel(f"offset from {args.freq / 1e6:.3f} MHz [kHz]")
    ax_wf.set_ylabel("time (newest at top)")
    fig.tight_layout()

    print("running — close the plot window or Ctrl-C to stop")
    try:
        while plt.fignum_exists(fig.number):
            # ~66 ms of signal per screen update; averages 16 FFTs so even a
            # single 117 ms packet burst lights up clearly.
            iq = sdr.read_iq(16 * n_fft)
            frames = iq.reshape(16, n_fft) * window
            spec = np.fft.fftshift(np.abs(np.fft.fft(frames, axis=1)) ** 2, axes=1)
            db = 10 * np.log10(spec.mean(axis=0) / (n_fft * n_fft) + 1e-12)

            waterfall = np.roll(waterfall, 1, axis=0)
            waterfall[0] = db
            line.set_ydata(db)
            im.set_data(waterfall[::-1])

            peak = int(np.argmax(db))
            ax_spec.set_title(
                f"peak {db[peak]:5.1f} dBFS at {freqs_khz[peak]:+7.1f} kHz offset "
                f"({(args.freq + freqs_khz[peak] * 1e3) / 1e6:.4f} MHz)")
            fig.canvas.draw_idle()
            plt.pause(0.001)
    except KeyboardInterrupt:
        pass
    finally:
        sdr.close()


if __name__ == "__main__":
    main()
